# dupefind

A safe duplicate-file finder for the terminal. It finds files with identical
content, tells you how much space they waste, and helps you get rid of the
extra copies **without ever deleting anything you have not reviewed**:

1. `dupefind scan` reports duplicates (read-only).
2. `dupefind plan` writes a JSON plan saying which copy to keep in each group.
3. You read (and, if you like, edit) the plan.
4. `dupefind apply` re-checks every file and moves the extras to a quarantine
   folder, or deletes them if you ask for `--delete`.
5. Changed your mind? `dupefind restore` moves quarantined files back.
6. Sure you won't? `dupefind purge --older-than 30d` empties old quarantines.

**Who it is for:** anyone cleaning up photo folders, old backups or download
directories who wants a tool that is careful by default and leaves a paper
trail.

**Built with:** Python 3.10+ and the standard library only (no runtime
dependencies). Tests use pytest. Works on Windows, Linux and macOS.

## Install

```sh
git clone https://github.com/Chad9100/dupefind.git
cd dupefind
python -m pip install .
```

That puts a `dupefind` command on your PATH. `python -m dupefind` works too.

## Usage

### Scan

```sh
dupefind scan PATH... [--min-size SIZE] [--include GLOB] [--exclude GLOB]
                      [--json] [--limit N] [--quiet] [--jobs N]
```

- `--min-size` ignores small files: `100`, `4K`, `1.5M`, `2GiB` (1K = 1024).
  The default is 1 byte, so empty files are skipped; `--min-size 0` includes them.
- `--include` / `--exclude` take shell-style globs and can be repeated. They
  match the file name or the path relative to the scanned root, so
  `--include "*.jpg"`, `--exclude .git` and `--exclude "build/*"` all work.
  `--exclude` also prunes whole folders.
- `--json` prints machine-readable output (all groups, sizes, mtimes, errors).
- `--limit N` caps how many groups the human report lists (default 20, 0 = all).
- While scanning, a single progress line on stderr shows files seen and bytes
  hashed so far. It is only drawn when stderr is a terminal (so pipes, logs and
  `--json` output stay clean) and is erased before the report prints.
  `-q` / `--quiet` turns it off; `plan` accepts the same flag.
- `-j N` / `--jobs N` hashes up to N files at once in threads (default 1).
  This can help on SSDs and network drives, where one reader leaves the disk
  idle; on a single spinning disk more jobs can be slower because the head
  has to seek between files. The groups found are identical for any N.
  `plan` accepts it too.

### Plan

```sh
dupefind plan PATH... [scan options] [--keep oldest|newest|shortest-path|first-in]
                      [-o dupefind-plan.json] [--force]
```

`--keep` decides which copy survives in each group:

| strategy        | keeps                                                        |
|-----------------|--------------------------------------------------------------|
| `oldest`        | the copy with the oldest modification time (default)         |
| `newest`        | the copy with the newest modification time                   |
| `shortest-path` | the copy with the shortest full path                         |
| `first-in`      | the copy under the earliest PATH you listed (`plan master backup` keeps the copy in `master`) |

Ties are broken by PATH order and then alphabetically, so the same tree always
gives the same plan. An existing plan file is never overwritten without
`--force`.

The plan is plain JSON. Each group has one `keep` path and a `remove` list.
To spare a file, delete it from `remove`; to leave a whole group alone, delete
the group.

### Apply

```sh
dupefind apply PLAN [--dry-run] [--quarantine DIR | --delete]
```

By default the extra copies are moved into `dupefind-quarantine/` next to the
plan file, keeping their folder structure (relative to the scanned root), and
every move is recorded in `dupefind-quarantine/manifest.jsonl` (original path,
new path, size, SHA-256, which copy was kept). `--quarantine DIR` picks another
folder. `--delete` removes the extras instead. `--dry-run` does all the checks
and prints what would happen without changing anything.

Exit status: `0` when everything in the plan was done, `1` when some files
were skipped (the rest still ran), `2` for a bad plan file or usage error.

### Restore

```sh
dupefind restore QUARANTINE_DIR [--dry-run]
```

Moves quarantined files back to their original paths, using the folder's
`manifest.jsonl`. Each file is re-checked first: it must still be a regular
file with the recorded size and SHA-256, and it must lie inside the quarantine
folder (a hand-edited manifest cannot make `restore` move files from
elsewhere). **An existing file at the original path is never overwritten**;
that record is skipped and reported. `--dry-run` shows what would happen.

After a restore the manifest lists only the files still in quarantine, so a
second run picks up where the first left off, and emptied subfolders are
removed. Exit status follows `apply`: `0` all restored, `1` some skipped,
`2` not a quarantine folder.

```text
$ dupefind restore dupefind-quarantine
restored  C:\work\photos\beach (copy).jpg
restored  C:\work\backup\photos\beach.jpg
skipped   C:\work\docs\report.pdf: a file now exists at the original path; not overwriting
Restored 2 files (6.0 MiB), skipped 1
```

### Purge

```sh
dupefind purge QUARANTINE_DIR --older-than AGE [--dry-run]
```

Permanently deletes quarantined files that were moved there more than `AGE`
ago, judged by the `time` each `apply` wrote to `manifest.jsonl`. `AGE` is a
number with a unit: `90m`, `12h`, `30d`, `2w` (`0d` purges everything).
Only files listed in the manifest are deleted, and each is checked first just
like `restore` does: it must lie inside the quarantine folder and still have
the recorded size and SHA-256, so a hand-edited manifest cannot point `purge`
at files elsewhere. Newer files stay, and the manifest is rewritten to list
only what remains. Exit status follows `restore`.

```text
$ dupefind purge dupefind-quarantine --older-than 30d --dry-run
would purge  C:\work\dupefind-quarantine\photos\beach.jpg
Dry run: would have purged 1 file (3.0 MiB), kept 2 newer

$ dupefind purge dupefind-quarantine --older-than 30d
purged  C:\work\dupefind-quarantine\photos\beach.jpg
Purged 1 file (3.0 MiB), kept 2 newer
```

### Example

Captured from a real run (the working folder prefix is shortened to `C:\work`):

```text
$ dupefind scan .
Scanned 6 files (9.5 MiB) in 1 root
Found 2 duplicate groups: 5 files, 3 redundant copies
Wasted space: 6.2 MiB

[1] 3.0 MiB x 3 (wasted 6.0 MiB)  sha256 11f907f1fea1
    C:\work\backup\photos\beach.jpg
    C:\work\photos\2024\beach.jpg
    C:\work\photos\beach (copy).jpg

[2] 244.1 KiB x 2 (wasted 244.1 KiB)  sha256 f470844e3ee0
    C:\work\backup\report-final.pdf
    C:\work\docs\report.pdf

$ dupefind plan photos backup docs --keep first-in
Wrote C:\work\dupefind-plan.json
  2 groups, 3 files to remove (keep: first-in), 6.2 MiB reclaimable
Review it, then run: dupefind apply dupefind-plan.json --dry-run

$ dupefind apply dupefind-plan.json --dry-run
would move  C:\work\photos\beach (copy).jpg -> C:\work\dupefind-quarantine\photos\beach (copy).jpg
would move  C:\work\backup\photos\beach.jpg -> C:\work\dupefind-quarantine\backup\photos\beach.jpg
would move  C:\work\docs\report.pdf -> C:\work\dupefind-quarantine\docs\report.pdf
Dry run: would have moved 3 files (6.2 MiB)

$ dupefind apply dupefind-plan.json
moved  C:\work\photos\beach (copy).jpg -> C:\work\dupefind-quarantine\photos\beach (copy).jpg
moved  C:\work\backup\photos\beach.jpg -> C:\work\dupefind-quarantine\backup\photos\beach.jpg
moved  C:\work\docs\report.pdf -> C:\work\dupefind-quarantine\docs\report.pdf
Moved 3 files (6.2 MiB)
Quarantine: C:\work\dupefind-quarantine

$ dupefind scan .
Scanned 4 files (3.2 MiB) in 1 root
No duplicates found.

$ dupefind apply dupefind-plan.json
skipped  C:\work\photos\beach (copy).jpg: no longer exists
skipped  C:\work\backup\photos\beach.jpg: no longer exists
skipped  C:\work\docs\report.pdf: no longer exists
Moved 0 files (0 B), skipped 3
```

The plan file from that run looks like this (paths shortened the same way):

```json
{
  "dupefind_plan": 1,
  "_readme": "Review before running 'dupefind apply'. ...",
  "created": "2026-11-10T15:33:01Z",
  "dupefind_version": "0.1.0",
  "keep_strategy": "first-in",
  "roots": ["C:\\work\\photos", "C:\\work\\backup", "C:\\work\\docs"],
  "summary": {"groups": 2, "files_to_remove": 3, "reclaimable_bytes": 6541456},
  "groups": [
    {
      "size": 3145728,
      "sha256": "11f907f1fea1bc73d3b75183b4f965153f5741caf589d110f680ec4c138c841a",
      "keep": "C:\\work\\photos\\2024\\beach.jpg",
      "remove": [
        "C:\\work\\photos\\beach (copy).jpg",
        "C:\\work\\backup\\photos\\beach.jpg"
      ]
    },
    ...
  ]
}
```

## How duplicates are found

Files are compared in three passes so most of them are never read in full:

1. **Size.** Files with a unique size cannot have a duplicate and are dropped
   without being opened.
2. **First 64 KiB.** Same-size files are hashed (SHA-256) over their first
   64 KiB. For files no larger than that, this already is the full hash.
3. **Full SHA-256.** Only files that still collide are hashed completely.

Hard links to the same physical file are counted once (deleting one would
free nothing).

## Safety

- **Nothing is deleted without a plan.** `scan` and `plan` never modify your
  files; only `apply PLAN` does, and only for paths listed in that plan.
  `purge` deletes only files that `apply` recorded in a quarantine manifest,
  after checking they are unchanged.
- **Quarantine by default.** `apply` moves files unless you pass `--delete`,
  and writes a manifest so every move can be undone with `dupefind restore`.
- **Re-verified before acting.** For each group the kept copy must still exist
  with the planned SHA-256, otherwise the whole group is skipped. Each extra
  copy must still be a regular file of the planned size and hash.
- **Never the last copy.** A path that is the `keep` of any group is never
  removed, a path listed twice is handled once, and a file that is the same
  physical file as its kept copy (a hard link, or the same file through a
  different spelling of its path) is skipped.
- **Symlinks are never followed.** Symlinks and Windows junctions are skipped
  during the scan, and a file that has become a symlink since the plan was
  written is skipped by `apply`.
- **Unreadable files are reported, not fatal.** Permission errors and files
  that vanish mid-scan are listed at the end of the report (or in `errors` in
  the JSON) and the scan carries on.
- **Quarantine is not rescanned.** The quarantine folder contains a
  `.dupefind-quarantine` marker file, and scans skip any folder that has one.
- Plan paths must be absolute; relative paths in a hand-edited plan are
  skipped.

Still, run `--dry-run` first and keep backups of anything you care about.

## Development

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # .venv/bin/python on Linux/macOS
pytest -q
```

CI runs the tests on Ubuntu and Windows with Python 3.10, 3.12 and 3.13.

See [ROADMAP.md](ROADMAP.md) for planned work.

## License

MIT, see [LICENSE](LICENSE).
