# Installing winch-data-processing

For use on a workstation, the README's [Install](README.md#install) is all there is: Python
3.11+, [uv](https://docs.astral.sh/uv/) and git, then `uv sync`. This page sets it up for
OpenVDM.

## Install for the OpenVDM hook

[OpenVDM](https://github.com/OceanDataTools/openvdm) runs the assessment from two of its hooks:
after each transfer of the collection system that logs the winches, during the cruise, and when
the cruise is finalized, for the final wire logs. Setting it up takes five steps: install, add
an extra directory for the outputs, write the ship's site TOML, add the hooks to
`openvdm.yaml`, and try it. The README's [OpenVDM section](README.md#openvdm) describes what the
hook does, where its values come from, troubleshooting, and running it on a past cruise.

It's the same setup as nav-qa-processing's and sbe-qa-processing's, so a ship running those
already has steps 1 and 4 in hand.

### 1. Install

Install it where OpenVDM's workers can run it, in its own environment (not OpenVDM's venv), with
the `openvdm` extra (pyyaml, to read `openvdm.yaml`). Do it as OpenVDM's user (`OPENVDM_USER`,
`survey` by default):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"   # uv in this shell
cd ~
git clone https://github.com/OceanDataTools/winch-data-processing.git
sudo mv ~/winch-data-processing /opt/
cd /opt/winch-data-processing
uv sync --no-dev --extra openvdm --no-managed-python   # the system Python 3.11+
```

The hook runs the venv's own Python, `.venv/bin/winch-data-processing`, never uv, so OpenVDM's
workers run it as root (or any user) without uv on their `PATH`. On **Ubuntu 22.04**, whose
Python is too old, install one with uv, kept beside the install:

```bash
export UV_PYTHON_INSTALL_DIR=/opt/winch-data-processing/python
uv python install 3.12
uv sync --no-dev --extra openvdm --python 3.12
```

To update, `git pull` in `/opt/winch-data-processing` and run the same `uv sync`.

### 2. Add an extra directory

In OpenVDM's web interface, add an extra directory for the outputs (Configuration > Extra
Directories): name `WINCH_QA`, destination e.g. `Products/WINCH_QA`. The hook writes each
winch's wire log, checks and config there, replacing the previous run's. Another name goes in
the site TOML's `[openvdm] output_extra_directory`.

### 3. Write the site TOML

OpenVDM knows the cruise (ID, name, PI, dates) and where each transfer's files go, but not which
of a collection system's files are which winch's, their format, or the winch's tension member,
sheaves and monitoring. The **site TOML** says so, once per ship: a `[[winch]]` table for each
winch, holding what a config TOML holds but `[cruise]`.

`site-config` writes it by asking for each field, and works offline:

```bash
cd /opt/winch-data-processing
.venv/bin/winch-data-processing site-config        # writes configs/site.toml
```

`configs/site.toml` in the install is where the hook reads it, so the hook commands below
needn't name it, and `site-config` writes it there from any directory. (`-o` writes another,
and the hook's `--site-config` reads another.)

It asks for the vessel's name and contact (from OpenVDM 2.17, `openvdm.yaml`'s `vessel` block
supplies the defaults), the extra directory from step 2, then each winch: its name (which names
its outputs, `<CRUISE>_<name>_...`), the collection system transfer it's in, its data format
and a glob of its files within that transfer's directory (e.g. `*hydro*.txt`), and its tension
member's NSF identifier, kind, NBL, selected factor of safety and diameter. Then add each
winch's sheaves (`[[winch.sheave]]`), monitoring system (`[winch.monitoring]`) and break tests
(`[[winch.tension_member.break_test]]`) to the file: `configs/site.example.toml` shows every
key, for Revelle's hydro and trawl winches. Run `site-config --force` to change the file (its
current values become the defaults, and what it doesn't ask is kept), or edit it. A misspelled
or misplaced key, or a winch whose tension member the standard doesn't allow (a cable at FS
1.8, say), is an error when the file is read.

To check the site TOML against OpenVDM's current cruise without assessing anything, print the
config the hook would use for each winch:

```bash
.venv/bin/winch-data-processing config-from-openvdm OpenRVDAS -o -
```

(`-o configs` writes a `<CRUISE>_<WINCH>.toml` for each instead; `-` takes one winch.)

### 4. Add the hooks to openvdm.yaml

The assessment runs from two of OpenVDM's hooks, set in its `openvdm.yaml`
(`/opt/openvdm/server/etc/openvdm.yaml` by default) under `postHookCommands`:
- **`postCollectionSystemTransfer`:** after each transfer of the collection system that logs the
  winches, it assesses the cruise so far, so a tension over the safe working tension or a
  tensiometer that stopped logging shows during the cruise.
- **`preFinalizeCurrentCruise`:** when the cruise is finalized, it writes the final wire logs,
  on the whole cruise. OpenVDM waits for it before finalizing, so they're in the finalized
  cruise and its MD5 summary.

`openvdm.yaml` already has both keys under `postHookCommands`, perhaps with nav-qa-processing's
or sbe-qa-processing's entries. Add these entries under those keys rather than writing the keys
again: YAML keeps only the last of two keys with one name, silently.

```yaml
postHookCommands:
    postCollectionSystemTransfer:
        - collectionSystemTransferName: OpenRVDAS
          commandList:
          - name: "Winch wire logs"
            command:
            - /opt/winch-data-processing/.venv/bin/winch-data-processing
            - openvdm
            - "{collectionSystemTransferName}"
            - "--openvdm-config=/opt/openvdm/server/etc/openvdm.yaml"
            - "--changed-files={newFiles}"
            - "--changed-files={updatedFiles}"
            - "--min-interval=1h"

    preFinalizeCurrentCruise:
        commandList:
        - name: "Winch wire logs"
          command:
          - /opt/winch-data-processing/.venv/bin/winch-data-processing
          - openvdm
          - OpenRVDAS
          - "--openvdm-config=/opt/openvdm/server/etc/openvdm.yaml"
```

| Line | What it is |
|---|---|
| `collectionSystemTransferName: OpenRVDAS` | The collection system transfer's name, exactly as in OpenVDM (Configuration > Collection System Transfers) |
| `/opt/.../winch-data-processing`, `openvdm` | The install from step 1, and the hook command |
| `"{collectionSystemTransferName}"` | OpenVDM fills in the transfer's name: the hook reads that transfer's directory, and assesses the winches whose `transfer` is it or blank |
| `OpenRVDAS`, in `preFinalizeCurrentCruise` | The same transfer's name, written out: `{collectionSystemTransferName}` has no value in that hook, and OpenVDM skips a command that uses it |
| (no `--site-config`) | The hook reads the site TOML from step 3 at `/opt/winch-data-processing/configs/site.toml`; give `--site-config=PATH` for one elsewhere |
| `--openvdm-config` | `openvdm.yaml` itself, for OpenVDM's web address (`siteRoot`) and `vessel` block. This path is the default; give yours if OpenVDM is installed elsewhere |
| `--changed-files={newFiles}`, `={updatedFiles}` | The transfer's new and updated files, so a winch is assessed only when its files changed. Keep the `--changed-files=` form: OpenVDM drops a bare `{newFiles}` argument when there are none. Not in `preFinalizeCurrentCruise`, which assesses the whole cruise whatever changed |
| `--min-interval=1h` | Skip a winch whose wire log is newer than this (`s`, `m`, `h` or `d`). Each run reads the whole cruise so far, and OpenVDM transfers every 5 minutes and runs its post hooks one at a time; a month at 20 Hz is 52 M records, about 30 s a winch (its memory follows the largest file, not the cruise). Not in `preFinalizeCurrentCruise`, whose wire logs are the final ones |

A ship whose winches are in several collection systems adds an entry for each transfer, and
gives each winch its `transfer` in the site TOML.

OpenVDM reads `openvdm.yaml` when its workers start, so restart the hook worker after editing it:

```bash
sudo supervisorctl restart openvdm:post_hooks
```

### 5. Try it

To run the hook now, give the same command by hand, as root (as OpenVDM runs it) and without
`--changed-files` or `--min-interval`, so it assesses every winch whatever changed. It's also
the pre-finalize hook's command, so this tries that one too:

```bash
sudo /opt/winch-data-processing/.venv/bin/winch-data-processing openvdm OpenRVDAS \
    --openvdm-config=/opt/openvdm/server/etc/openvdm.yaml
```

It prints each winch it found, the checks, and the files it wrote into the extra directory.
OpenVDM logs its runs in `/var/log/openvdm/post_hooks.log`, and a failure shows in OpenVDM as
the hook's one-line message. The README's [Troubleshooting](README.md#troubleshooting) table
says what each message means.
