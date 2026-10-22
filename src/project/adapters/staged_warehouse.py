"""Warehouse staging: a run builds on a copy of the warehouse, and the copy replaces the live file only when the run succeeds."""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# SQLMesh resolves `config.yaml`'s relative database against the working directory, so a run that starts in `build_root` finds the copy
# at `data/warehouse.duckdb` below it, the same relative path the live warehouse has below the project.
RELATIVE_WAREHOUSE: Path = Path("data") / "warehouse.duckdb"
FAILED_PATTERN: str = "warehouse.failed-*.duckdb"


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StagedWarehouse:
    """Gives a run a private copy of `<root>/data/warehouse.duckdb` to build on, then commits or discards it.

    `commit()` replaces the live file with the copy. `discard()` leaves the live file untouched and keeps the copy as
    `<root>/data/warehouse.failed-<UTC timestamp>.duckdb`, so the rows that failed an audit stay queryable without ever being visible in
    the live warehouse. Only the newest `keep` failed copies are kept; a successful run leaves them alone.
    """

    root: Path
    keep: int = 3
    clock: Callable[[], datetime] = utc_now

    @property
    def live(self) -> Path:
        """The warehouse that consumers read."""
        return self.root / RELATIVE_WAREHOUSE

    @property
    def build_root(self) -> Path:
        """The directory a run uses as its working directory."""
        return self.live.parent / ".build"

    @property
    def build(self) -> Path:
        """The copy a run builds on."""
        return self.build_root / RELATIVE_WAREHOUSE

    def failed_copies(self) -> list[Path]:
        """Return the kept copies of failed runs, oldest first."""
        return sorted(self.live.parent.glob(FAILED_PATTERN))

    def prepare(self) -> Path:
        """Create the build directory with a copy of the live warehouse (none on the first run) and return it.

        What an interrupted run left in the build directory is removed first.
        """
        shutil.rmtree(self.build_root, ignore_errors=True)
        self.build.parent.mkdir(parents=True)
        if self.live.exists():
            shutil.copy2(self.live, self.build)
            wal: Path = wal_of(self.live)
            if wal.exists():
                shutil.copy2(wal, wal_of(self.build))
        return self.build_root

    def commit(self) -> None:
        """Replace the live warehouse with the build, and remove the build directory."""
        wal_of(self.live).unlink(missing_ok=True)
        self.build.replace(self.live)
        shutil.rmtree(self.build_root)

    def discard(self) -> None:
        """Keep the build as a timestamped failed copy, drop the oldest copies beyond `keep`, and remove the build directory.

        The live warehouse is untouched.
        """
        if self.build.exists():
            stamp: str = self.clock().astimezone(UTC).strftime("%Y%m%dT%H%M%S%f")
            self.build.replace(self.live.with_name(f"warehouse.failed-{stamp}.duckdb"))
        shutil.rmtree(self.build_root)
        stale: Path
        for stale in self.failed_copies()[: -self.keep]:
            stale.unlink()


def wal_of(database: Path) -> Path:
    """Return the write-ahead log DuckDB keeps next to `database` after an unclean shutdown."""
    return database.with_name(database.name + ".wal")
