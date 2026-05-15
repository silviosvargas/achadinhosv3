# -*- mode: python ; coding: utf-8 -*-
"""
Configuração do PyInstaller pra gerar AchadinhosAgent.exe.

Build:
    pyinstaller build.spec
    # → dist/AchadinhosAgent.exe (~40-60 MB com Selenium + undetected)

Notas:
- console=True (mantemos console por enquanto pra ver logs em dev/troubleshooting).
  Quando virar produção pra usuário final, mudar pra console=False e usar tray icon.
- icon: usar .ico próprio quando tiver (placeholder None por enquanto)
- hidden imports: pystray, pillow, undetected_chromedriver e o pacote agent.*
  precisam de coleta explícita.

TODO Fase 6.1 — exes auxiliares:
    pyinstaller --onefile agent/setup.py        --name AchadinhosSetup
    pyinstaller --onefile agent/login_ml.py     --name AchadinhosLoginML
    pyinstaller --onefile agent/login_whatsapp.py --name AchadinhosLoginWA
    # Usuário final vai precisar de todos esses .exe juntos, ou empacotar
    # tudo num único exe com subcomandos (preferível).
"""

block_cipher = None

a = Analysis(
    ['agent/main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pystray._win32',     # backend Windows do pystray
        'PIL._tkinter_finder',
        # Módulos do próprio agente que podem não ser detectados via import estático
        'agent.busca_ml',
        'agent.ingest_client',
        'agent.chrome',
        'agent.local_server',         # Fase 9.2 — HTTP local
        'agent.postador.whatsapp',
        'agent.postador.saude',
        # undetected-chromedriver tem imports dinâmicos
        'undetected_chromedriver',
        'undetected_chromedriver.cdp',
        'undetected_chromedriver.options',
        'undetected_chromedriver.patcher',
        'undetected_chromedriver.reactor',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Reduz tamanho excluindo coisas que não usamos
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AchadinhosAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,         # console=True em dev p/ ver logs; False quando virar produção
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,             # TODO: substituir por agent/assets/icon.ico
)
