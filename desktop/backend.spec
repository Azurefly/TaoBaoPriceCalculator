# Built natively on each target OS / architecture. No user data included.
from pathlib import Path
root = Path(SPECPATH).parent
analysis = Analysis([str(root / 'desktop/entry.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'app/static'), 'app/static'),
                       (str(root / 'examples/demo.json'), 'examples'),
                       (str(root / 'examples/historical.json'), 'examples')],
    hiddenimports=['uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
                   'uvicorn.lifespan.on', 'playwright.async_api'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'IPython', 'matplotlib', 'numpy', 'pandas', 'pytest'], noarchive=False)
archive = PYZ(analysis.pure)
executable = EXE(archive, analysis.scripts, [], exclude_binaries=True,
    name='TaoBaoPriceCalculator-service', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True)
collection = COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False,
                     name='TaoBaoPriceCalculator-service')
