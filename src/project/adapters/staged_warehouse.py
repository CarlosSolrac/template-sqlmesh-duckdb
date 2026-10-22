"""Warehouse staging: a run builds on a copy of the warehouse, and the copy replaces the live file only when the run succeeds."""

import shutil
from dataclasses import dataclass
from pathlib import Path

# SQLMesh resolves `config.yaml`'s relative database against the working directory, so a run that starts in `build_root` finds the copy
# at `data/warehouse.duckdb` below it, the same relative path the live warehouse has below the project.
RELATIVE_WAREHOUSE: Path = Path("data") / "warehouse.duckdb"


@dataclass(frozen=True, slots=True)
class StagedWarehouse:
    """Gives a run a private copy of `<root>/data/warehouse.duckdb` to build on, then commits or discards it.

    `commit()` replaces the live file with the copy. `discard()` leaves the live file untouched and keeps the copy as
    `<root>/data/warehouse.failed.duckdb`, so the rows that failed an audit stay queryable without ever being visible in the live
    warehouse.
    """

    root: Path

    @property
    def live(self) -> Path:
        """The warehouse that consumers read."""
        return self.root / RELATIVE_WAREHOUSE

    @property
    def failed(self) -> Path:
        """Where the copy of the latest failed run is kept."""
        return self.live.with_name("warehouse.failed.duckdb")

    @property
    def build_root(self) -> Path:
        """The directory a run uses as its working directory."""
        return self.live.parent / ".build"

    @property
    def build(self) -> Path:
        """The copy a run builds on."""
        return self.build_root / RELATIVE_WAREHOUSE

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
        """Replace the live warehouse with the build, and remove the failed copy of an earlier run and the build directory."""
        wal_of(self.live).unlink(missing_ok=True)
        self.build.replace(self.live)
        self.failed.unlink(missing_ok=True)
        shutil.rmtree(self.build_root)

    def discard(self) -> None:
        """Keep the build as the failed copy, replacing an earlier one, and remove the build directory; the live warehouse is untouched."""
        if self.build.exists():
            self.build.replace(self.failed)
        shutil.rmtree(self.build_root)


def wal_of(database: Path) -> Path:
    """Return the write-ahead log DuckDB keeps next to `database` after an unclean shutdown."""
    return database.with_name(database.name + ".wal")
