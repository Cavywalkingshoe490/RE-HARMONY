"""Package for the RE-HARMONY desktop app.

## Architecture, in one page

The app is a **thin shell over the modules in `config_work/`**. It
re-implements nothing on the critical path: every operation that touches a
blob or the remote is `config_work/` code, invoked **as a subprocess**, with
its own gate. The app builds the command line, shows the output verbatim,
and records what happened.

    main.py        opens the window (pywebview) or the headless bridge
    api.py         the ONLY class exposed to the JS -- everything goes
                   through here; it delegates, it does not reimplement
    session.py      login + password in the OS keychain
    catalog.py    the vendor's public catalog (read-only)
    library.py  the IR protocols already captured on disk, and
                   materializing a device from them
    ir_manual.py   import a `.ir` (Flipper Zero / IRDB) with no account and
                   no internet -- parsed by `config_work/read_ir.py`
    generate.py     add_device.py / list_devices.py / delete_device.py /
                   screen_activities.py / edit_activity.py as
                   subprocesses, plus the pure gate
    remote.py     read_config.py as a subprocess, and BUILDING (not running) the
                   write command line
    history.py    SQLite history of what was written, with rollback
    ui/            index.html + app.css + app.js -- no framework, no CDN

## The three rules the layering exists to enforce

1. **One direction only.** `app/` imports `config_work/`; `config_work/`
   never imports `app/`. Anything the app needs from a blob is either a
   subprocess call or a pure function.
2. **Nothing writes to flash from inside `app/`.** `remote.py` returns a
   `RecordLine` (data); it never runs `write.py`. Running it has to be an
   explicit act by whoever holds the terminal.
3. **The gate is called, never copied.** `grabar.nothing_moved` and
   `grabar.ALLOWED` are the same objects `write.py` itself uses.

Every screen has a `control_*.py` next to it that exercises it with its
NEGATIVE case; see `app/README.md` for the full table of what each one
proves and what it deliberately does not.
"""
