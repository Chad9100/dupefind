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
                      [--json | --csv] [--limit N] [--sort KEY] [--quiet] [--jobs N] [--cache FILE]
                      [--one-file-system]
```

- `--min-size` ignores small files: `100`, `4K`, `1.5M`, `2GiB` (1K = 1024).
  The default is 1 byte, so empty files are skipped; `--min-size 0` includes them.
- `--include` / `--exclude` take shell-style globs and can be repeated. They
  match the file name or the path relative to the scanned root, so
  `--include "*.jpg"`, `--exclude .git` and `--exclude "build/*"` all work.
  `--exclude` also prunes whole folders.
- `--json` prints machine-readable output (all groups, sizes, mtimes, errors).
- `--csv` prints one row per duplicate file for spreadsheets (all groups, no
  `--limit`; mutually exclusive with `--json`). Columns: `group`, `size_bytes`,
  `sha256`, `copies`, `wasted_bytes` (of the group), `mtime` (Unix seconds),
  `path`. Fields with commas are quoted. `--sort` applies. Captured on a temp
  tree (folder prefix shortened to `C:\work`):

  ```text
  $ dupefind scan photos backup --csv
  group,size_bytes,sha256,copies,wasted_bytes,mtime,path
  1,3000,e1630f843370f402870799e14abbf2b06af2d23b0153658e1211dffabc61ad8f,2,3000,1794430365.8724089,C:\work\photos\each.jpg
  1,3000,e1630f843370f402870799e14abbf2b06af2d23b0153658e1211dffabc61ad8f,2,3000,1794430365.9493525,C:\work\backup\each.jpg
  2,12,a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447,2,12,1794430365.9508574,"C:\work\photos\notes, copy.txt"
  2,12,a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447,2,12,1794430365.9508574,C:\work\photos\notes.txt
  ```
- `--by-folder` replaces the group list with a summary of which folders hold
  the most duplicate data: for each folder, the number of files in it that
  have an identical twin somewhere and their total size (every copy counts,
  including the one you would keep). Biggest first; `--top N` limits the rows.
  Human report only. Captured on a temp tree (prefix shortened to `C:\work`):

  ```text
  $ dupefind scan photos backup docs --by-folder
  Folders holding duplicate data (every copy counted):

     5.9 KiB    2 files  C:\work\backup
     2.9 KiB    2 files  C:\work\photos
        16 B     1 file  C:\work\docs
  ```
- `--limit N` (alias `--top N`) caps how many groups the human report lists
  (default 20, 0 = all).
- `--sort wasted|size|count|path` orders the groups (human report and `--json`).
  `wasted` (the default) puts the biggest savings first, `size` the largest
  files, `count` the most copies, `path` alphabetical by first path. Combine
  with `--top`, e.g. `--sort size --top 5`.
- While scanning, a single progress line on stderr shows files seen and bytes
  hashed so far. It is only drawn when stderr is a terminal (so pipes, logs and
  `--json` output stay clean) and is erased before the report prints.
  `-q` / `--quiet` turns it off; `plan` accepts the same flag.
- `-j N` / `--jobs N` hashes up to N files at once in threads (default 1).
  This can help on SSDs and network drives, where one reader leaves the disk
  idle; on a single spinning disk more jobs can be slower because the head
  has to seek between files. The groups found are identical for any N.
  `plan` accepts it too.
- `--one-file-system` does not enter folders that live on a different device
  than the folder being scanned (mount points, other drives mounted inside
  the tree), like `find -xdev`. Each PATH is compared with its own device.
  The report format does not change; files below a skipped mount are simply
  not seen. The tests simulate a mount by faking the device id, because a
  real second file system cannot be created portably; it was not tried on a
  real mount point. `plan` accepts it too.
- `--cache FILE` keeps a JSON file of file hashes. On the next scan, a file
  whose path, size and modification time are unchanged is not read again, so
  repeat scans of a big tree are much faster. The file is created if missing,
  rewritten atomically, and ignored with a warning if it is unreadable. A
  changed size or mtime always forces a re-hash; `apply` never uses the cache
  and re-hashes every file itself. `plan` accepts it too.

  Checked on a temp tree of three files (two identical 4.9 KiB files, one
  unique). Two scans with the same `--cache cache.json` print the same report:

  ```text
  Scanned 3 files (9.8 KiB) in 1 root
  Found 1 duplicate group: 2 files, 1 redundant copy
  Wasted space: 4.9 KiB
  ```

  The second run read the cache: after setting the cached hash of one file to
  a wrong value (same size and mtime), the next scan reported
  `No duplicates found.`, so the stored hash was used instead of reading the
  file. After `touch` on that file (new mtime) the scan re-hashed it and
  found the duplicate group again.

### Plan

```sh
dupefind plan (PATH... | --from-scan SCAN_JSON) [scan options] [--keep STRATEGY[,STRATEGY...]]
                      [--keep-pattern GLOB] [--remove-pattern GLOB]
                      [-o dupefind-plan.json] [--force]
```

`--keep` decides which copy survives in each group:

| strategy        | keeps                                                        |
|-----------------|--------------------------------------------------------------|
| `oldest`        | the copy with the oldest modification time (default)         |
| `newest`        | the copy with the newest modification time                   |
| `shortest-path` | the copy with the shortest full path                         |
| `first-in`      | the copy under the earliest PATH you listed (`plan master backup` keeps the copy in `master`) |

Strategies can be combined with commas, and later ones only break ties left by
earlier ones: `--keep first-in,oldest` keeps the copy under the earliest PATH
and, if several copies sit under that PATH, the oldest of those.

`--keep-pattern GLOB` and `--remove-pattern GLOB` (both repeatable) override the
strategy by path. Copies matching a `--keep-pattern` are the only candidates to
keep; then copies matching a `--remove-pattern` are dropped from the
candidates. Globs match the file name or the full path (written with `/`), so
`--remove-pattern "*/Downloads/*"` always removes the Downloads copy when
another copy exists. A pattern that would leave no candidate in a group is
ignored for that group, so one copy is always kept; the `--keep` strategy then
picks among the remaining candidates.

`--from-scan SCAN_JSON` builds the plan from saved `dupefind scan PATH --json > scan.json`
output instead of scanning again (do not pass PATH). Useful for a slow scan you
want to plan several ways. `first-in` follows the root order of the saved scan.
The files are not re-read when planning; `apply` still re-hashes every file, so
a stale scan can only cause skips, never a wrong delete.

Ties are broken by PATH order and then alphabetically, so the same tree always
gives the same plan. An existing plan file is never overwritten without
`--force`.

The plan is plain JSON. Each group has one `keep` path and a `remove` list.
To spare a file, delete it from `remove`; to leave a whole group alone, delete
the group.

### Apply

```sh
dupefind apply PLAN [--dry-run] [--quarantine DIR | --delete | --hardlink | --symlink]
```

By default the extra copies are moved into `dupefind-quarantine/` next to the
plan file, keeping their folder structure (relative to the scanned root), and
every move is recorded in `dupefind-quarantine/manifest.jsonl` (original path,
new path, size, SHA-256, which copy was kept). `--quarantine DIR` picks another
folder. `--delete` removes the extras instead. `--dry-run` does all the checks
and prints what would happen without changing anything.

`--hardlink` replaces each extra copy with a hard link to the kept copy, so
every path still opens but the data is stored once. It only works on the same
volume (checked first; other files are skipped) and on file systems that
support hard links; if creating the link fails, the original file is left
untouched and reported. `--symlink` does the same with symbolic links (on
Windows this needs Developer Mode or an elevated prompt). Neither can be
combined with `--delete` or `--quarantine`, and nothing is quarantined, so
there is nothing to `restore`. The link is made under a temporary name and
renamed over the extra, so the path is never missing. Captured on throwaway
files in a temp folder (prefix shortened to `C:\work`):

```text
$ dupefind apply plan.json --hardlink --dry-run
would link  C:\work\backup\each.jpg -> C:\work\photos\each.jpg
would link  C:\work\photos\notes.txt -> C:\work\photos\notes, copy.txt
Dry run: would have linked 2 files (2.9 KiB)
$ dupefind apply plan.json --hardlink
linked  C:\work\backup\each.jpg -> C:\work\photos\each.jpg
linked  C:\work\photos\notes.txt -> C:\work\photos\notes, copy.txt
Linked 2 files (2.9 KiB)
$ dupefind apply plan.json --hardlink
skipped  C:\work\backup\each.jpg: is the same physical file as the kept copy
skipped  C:\work\photos\notes.txt: is the same physical file as the kept copy
Linked 0 files (0 B), skipped 2
```

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
  `--hardlink` / `--symlink` are opt-in and cannot be undone by `restore`.
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
