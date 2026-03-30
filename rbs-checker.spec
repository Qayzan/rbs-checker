import os
import playwright

# Path to the playwright driver bundled with the Python package
_playwright_driver = os.path.join(os.path.dirname(playwright.__file__), 'driver')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        (_playwright_driver, 'playwright/driver'),
    ],
    hiddenimports=[
        'playwright',
        'playwright.sync_api',
        'playwright._impl._sync_context_manager',
        'playwright._impl._api_types',
        'playwright._impl._connection',
        'flask',
        'flask.templating',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.routing',
        'jinja2',
        'click',
        'itsdangerous',
        'markupsafe',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='rbs-checker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,   # keep console so first-run download progress is visible
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='rbs-checker',
)
