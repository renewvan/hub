# scripts/

## `push-basemap.sh <file> [--host <ssh-alias>]`

Replaces the dashboard's **Basemap** on a Pi safely (see `CONTEXT.md`). Needs the `pmtiles` CLI
locally and the Pi bootstrapped and deployed (`bin/deploy.sh`). `--host` defaults to `renewvan`,
like `bin/deploy.sh`.

### Smoke procedure

There is no shell-test framework in this repo, so re-verify by hand after changing the script or
the `bin/deploy.sh` Basemap block. Prefer a real Pi: on a laptop (macOS) the single-file bind-mount
inode behaviour differs from native Linux, so a laptop run checks the script's flow only, and the
Pi stays authoritative for the restart requirement.

Setup: `A.pmtiles` and `B.pmtiles` are two valid, visibly different Basemaps (`pmtiles verify`
passes); `bad.pmtiles` is `head -c 100000 A.pmtiles`. `P=/etc/renewvan/dashboard/basemap.pmtiles`,
`H` is the ssh host. A throwaway container works too: run the dashboard image with a single-file
mount of `P` and point `--host` at a host whose `curl http://localhost/...` reaches it.

| #   | Do                                                                                                                                        | Expect                                                                                                                                                                        |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | On a Pi with no `/etc/renewvan/dashboard`: `bin/deploy.sh`                                                                                | `P` is an empty **file** (`ssh H stat -c %F P` is `regular empty file`), owned by the login user; stack up                                                                    |
| 2   | `ssh H curl -si http://localhost/maps/basemap.pmtiles` (placeholder)                                                                      | `200`, `Content-Length: 0` (not `301`); the Map page shows its "no map" state, not a broken map                                                                               |
| 3   | `scripts/push-basemap.sh A.pmtiles`                                                                                                       | exit 0; last line says `206`; `ssh H curl -s -H 'Range: bytes=0-15' ...` returns the first 16 bytes of `A`                                                                    |
| 4   | `bin/deploy.sh` again                                                                                                                     | `P` still has `A`'s size and checksum (deploy never overwrites an existing Basemap)                                                                                           |
| 5   | `scripts/push-basemap.sh bad.pmtiles`                                                                                                     | non-zero exit, `pmtiles verify` error; `P` unchanged, dashboard still serves `A`                                                                                              |
| 6   | `scripts/push-basemap.sh B.pmtiles` with the Pi's free space below 2x `B` (fill the disk, or push a file larger than half the free space) | non-zero exit, free-space error; `P` unchanged; no `P.tmp.*` left                                                                                                             |
| 7   | Start `scripts/push-basemap.sh B.pmtiles`, Ctrl-C during the copy                                                                         | exit 130; `ssh H ls P.tmp.*` finds nothing; `P` still `A`; dashboard still serves `A`                                                                                         |
| 8   | `scripts/push-basemap.sh B.pmtiles` twice in a row                                                                                        | both exit 0; dashboard serves `B` after each; no `P.tmp.*` left                                                                                                               |
| 9   | `scripts/push-basemap.sh B.pmtiles --host nonexistent`                                                                                    | non-zero exit with a clear ssh error; nothing changed                                                                                                                         |
| 10  | On a Pi where Docker already created a **directory** at `P`: `scripts/push-basemap.sh B.pmtiles`                                          | non-zero exit naming the directory; nothing copied, `P` unchanged. `bin/deploy.sh` removes an empty one and creates the placeholder; a non-empty one is refused, content kept |

On a real Pi also confirm the reason for the restart once: `mv` a new file over `P` by hand and
`curl` the range request; the container keeps serving the old file until `docker restart
renewvan-dashboard`.
