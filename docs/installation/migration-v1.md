# Migrating from v1

Agent Bridge v2 stores task data in a local SQLite database and retains a
bounded importer for v1 JSON boards. Preserve the old directory until the
import report has been reviewed.

```bash
bridge migrate path/to/v1-board.json
bridge status --oneliner
bridge export backup-after-migration.json
```

The importer records the imported task, agent, and delivery counts and creates
a backup of the v1 input. It does not overwrite the source JSON board. Re-run
the destination checks with `bridge doctor --strict` and inspect `bridge board`.

`bridge uninstall` preserves task data by default. To remove the v2 data after
you have exported or backed it up, run `bridge uninstall --purge-data` and
confirm the printed exact data-root path.

V1 custom launch commands are not blindly converted to shell text. Recreate
them as explicit, local argv configuration after validating the target host.
