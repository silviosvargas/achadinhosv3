"""
Postagem em grupos do WhatsApp Web via Selenium + pyautogui.

Portado de V2/src/postar/whatsapp.py.

Diferenças em relação à V2:
- Não recebe lista de grupos — 1 chamada = 1 postagem em 1 grupo
- Recebe imagem por URL (baixa pra disco temporário) ou caminho local
- Retorna dict {"ok": bool, "erro": str?, "tentar_de_novo": bool?}
- Não importa nada de web.jobs ou src.core (independente)
- Driver é reutilizado entre chamadas (cache em módulo)

Mantém 100% das técnicas anti-falha:
- _forcar_foco_chrome() via Win32 (AttachThreadInput)
- Retry de cola da imagem (3 tentativas, recopia clipboard)
- 3 estratégias de enviar (botão click + ActionChains + JS click + Enter)
- Reset incremental do estado WhatsApp em busca de grupo (3 camadas)
"""
from __future__ import annotations

import io
import os
import random
import tempfile
import time
from typing import Any

import httpx
import pyautogui
import pyperclip
import structlog
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from agent.chrome import garantir_chrome
from agent.postador import saude

# pywin32 só existe em Windows
try:
    import ctypes
    import win32clipboard
    import win32con
    import win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


pyautogui.FAILSAFE = False  # evita aborto se mouse vai pro canto

log = structlog.get_logger(__name__)


# ── Seletores CSS do WhatsApp Web ────────────────────
SELETOR_BUSCA       = "input[placeholder='Pesquisar ou começar uma nova conversa']"
SELETOR_CAMPO       = "div[contenteditable='true'][data-tab='10']"
SELETOR_BOTAO_ENVIAR = "span[data-icon='wds-ic-send-filled']"


# ── Cache do driver no módulo ────────────────────────
# O agente fica rodando, então mantemos o driver vivo entre postagens.
# Reconectar a cada postagem é caro (~500ms) e desnecessário.
_driver: webdriver.Chrome | None = None
_chrome_porta: int = 9222
_chrome_perfil: str = ""
_wa_handle: str | None = None


def configurar(*, porta: int, perfil: str) -> None:
    """Configura porta e perfil do Chrome. Chamar uma vez no boot do agente."""
    global _chrome_porta, _chrome_perfil
    _chrome_porta = porta
    _chrome_perfil = perfil


# ============================================================
# Interface pública
# ============================================================

async def postar(
    *,
    grupo_nome: str,
    texto: str,
    imagem_url: str | None = None,
) -> dict[str, Any]:
    """
    Posta uma mensagem (texto + imagem opcional) num grupo do WhatsApp.

    Retorno (sempre dict):
        {"ok": True, "duracao_ms": N}
        {"ok": False, "erro": "<codigo>", "tentar_de_novo": bool, "detalhes": str}
    """
    inicio = time.time()

    # Saúde — se já estamos no limite, recusa
    if saude.fator_pausa() >= 3.0:
        return _erro("saude_pausada", tentar_de_novo=True,
                     detalhes="muitas falhas seguidas — pausa adaptativa")

    if not HAS_WIN32:
        return _erro("sem_win32", tentar_de_novo=False,
                     detalhes="postagem WhatsApp só roda em Windows")

    # 1) Garante Chrome aberto e Selenium conectado
    driver = _obter_driver()
    if driver is None:
        return _erro("chrome_indisponivel", tentar_de_novo=True)

    # 2) Garante WhatsApp Web aberto
    handle = _obter_handle_whatsapp(driver)
    if handle is None:
        return _erro("whatsapp_nao_aberto", tentar_de_novo=False,
                     detalhes="abra o Chrome e escaneie o QR do WhatsApp Web")

    # 3) Baixa imagem (se houver)
    foto_path: str | None = None
    if imagem_url:
        foto_path = await _baixar_imagem(imagem_url)
        if foto_path is None:
            return _erro("imagem_download_falhou", tentar_de_novo=True,
                         detalhes=f"falha baixando {imagem_url}")

    try:
        # 4) Abre o grupo
        if not _abrir_grupo(driver, handle, grupo_nome):
            saude.registrar_falha(motivo=f"grupo:{grupo_nome}")
            return _erro("grupo_nao_encontrado", tentar_de_novo=False,
                         detalhes=f"grupo '{grupo_nome}' nao achado pela busca")

        # 5) Envia
        if foto_path:
            ok = _enviar_post_com_imagem(driver, foto_path, texto)
        else:
            ok = _enviar_post_so_texto(driver, texto)

        if not ok:
            deve_abortar = saude.registrar_falha(motivo=f"envio:{grupo_nome}")
            if deve_abortar:
                return _erro("deve_abortar", tentar_de_novo=False,
                             detalhes="saude do whatsapp piorando")
            return _erro("envio_falhou", tentar_de_novo=True)

        saude.registrar_sucesso()

        # Pausa adaptativa
        pausa = random.uniform(2.5, 4.5) * saude.fator_pausa()
        time.sleep(pausa)

        return {
            "ok": True,
            "duracao_ms": int((time.time() - inicio) * 1000),
        }

    finally:
        if foto_path and os.path.exists(foto_path):
            try:
                os.unlink(foto_path)
            except OSError:
                pass


def verificar_whatsapp() -> dict[str, Any]:
    """Diagnóstico — útil pra dashboard mostrar saúde do agente."""
    if not HAS_WIN32:
        return {"ok": False, "detalhes": "sem_win32"}
    driver = _obter_driver()
    if driver is None:
        return {"ok": False, "detalhes": "chrome_nao_conecta"}
    handle = _obter_handle_whatsapp(driver)
    if handle is None:
        return {"ok": False, "detalhes": "whatsapp_nao_aberto"}
    return {"ok": True, "detalhes": "tudo_certo"}


# ============================================================
# Internals — driver + WhatsApp
# ============================================================

def _obter_driver() -> webdriver.Chrome | None:
    """Retorna driver cached, conectando se necessário."""
    global _driver

    if _driver is not None:
        try:
            _ = _driver.window_handles  # ping
            return _driver
        except Exception:
            log.warning("driver.morto_reconectando")
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None

    _driver = garantir_chrome(porta=_chrome_porta, perfil=_chrome_perfil)
    return _driver


def _obter_handle_whatsapp(driver: webdriver.Chrome) -> str | None:
    """Retorna handle do WhatsApp Web, abrindo se preciso."""
    global _wa_handle

    if _wa_handle and _wa_handle in driver.window_handles:
        try:
            driver.switch_to.window(_wa_handle)
            if "web.whatsapp.com" in driver.current_url:
                return _wa_handle
        except Exception:
            _wa_handle = None

    _wa_handle = _abrir_whatsapp(driver)
    return _wa_handle


def _abrir_whatsapp(driver: webdriver.Chrome) -> str | None:
    """Abre/encontra aba do WhatsApp Web logada. Detecta QR pendente."""
    wait = WebDriverWait(driver, 60)

    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            if "web.whatsapp.com" in driver.current_url:
                wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, SELETOR_BUSCA)))
                log.info("whatsapp.ja_aberto")
                return driver.current_window_handle
        except Exception:
            continue

    driver.execute_script("window.open('https://web.whatsapp.com');")
    driver.switch_to.window(driver.window_handles[-1])
    log.info("whatsapp.abrindo")

    try:
        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, SELETOR_BUSCA)))
        log.info("whatsapp.carregou")
        return driver.current_window_handle
    except Exception:
        try:
            tem_qr = bool(driver.find_elements(By.CSS_SELECTOR,
                "canvas[aria-label*='Scan' i], canvas[aria-label*='Escane' i], "
                "div[data-ref], div[data-testid='qrcode']"))
        except Exception:
            tem_qr = False
        if tem_qr:
            log.error("whatsapp.qr_pendente")
        else:
            log.error("whatsapp.nao_carregou")
        return None


def _abrir_grupo(driver: webdriver.Chrome, wa_handle: str, nome_grupo: str) -> bool:
    """Busca e abre grupo pelo nome. 3 tentativas com reset progressivo."""
    driver.switch_to.window(wa_handle)

    for tentativa in range(1, 4):
        wait = WebDriverWait(driver, 15)
        try:
            # Reset camada 1: ESC pra fechar modais
            try:
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.3)
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.3)
            except Exception:
                pass

            # Reset camada 2: clica fora
            if tentativa >= 2:
                try:
                    driver.execute_script("""
                        var header = document.querySelector('header[role="banner"]') ||
                                     document.querySelector('div[data-testid="chatlist-header"]');
                        if (header) header.click();
                    """)
                    time.sleep(0.5)
                    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    time.sleep(0.4)
                except Exception:
                    pass

            # Reset camada 3: foco direto via JS (evita teclado BR)
            if tentativa >= 3:
                try:
                    driver.execute_script("""
                        var sel = document.querySelector('div[contenteditable="true"][data-tab="3"]') ||
                                  document.querySelector('div[role="textbox"][data-tab="3"]');
                        if (sel) {
                            sel.focus();
                            sel.click();
                        }
                    """)
                    time.sleep(0.5)
                except Exception:
                    pass

            busca = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, SELETOR_BUSCA)))
            try:
                busca.click()
            except Exception:
                driver.execute_script(
                    "arguments[0].click(); arguments[0].focus();", busca)
            time.sleep(0.5)

            # Limpa busca (3 estratégias)
            try:
                busca.clear()
            except Exception:
                pass
            try:
                busca.send_keys(Keys.CONTROL + "a")
                time.sleep(0.15)
                busca.send_keys(Keys.DELETE)
                time.sleep(0.15)
            except Exception:
                pass
            try:
                driver.execute_script("""
                    var el = arguments[0];
                    el.innerText = '';
                    el.textContent = '';
                    el.dispatchEvent(new InputEvent('input', {bubbles: true, cancelable: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                """, busca)
            except Exception:
                pass
            time.sleep(0.5)

            try:
                busca.send_keys(nome_grupo)
            except Exception:
                ActionChains(driver).send_keys(nome_grupo).perform()
            time.sleep(3)  # filtro renderizar

            wait.until(EC.element_to_be_clickable((
                By.XPATH,
                f"//span[@title='{nome_grupo}'] | //span[contains(@title,'{nome_grupo}')]"
            ))).click()
            time.sleep(2.5)

            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, SELETOR_CAMPO)))
            return True

        except Exception as e:
            erro = str(e)[:120]
            if tentativa < 3:
                log.warning("grupo.tentativa_falhou",
                            tentativa=tentativa, grupo=nome_grupo, erro=erro)
                time.sleep(2)
                try:
                    driver.switch_to.window(wa_handle)
                    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    time.sleep(1)
                except Exception:
                    pass
                continue
            log.error("grupo.nao_aberto", grupo=nome_grupo, erro=erro)
            return False
    return False


def _enviar_post_com_imagem(driver: webdriver.Chrome,
                            foto_path: str, texto: str) -> bool:
    """Envia post (imagem + texto) no grupo aberto."""
    wait = WebDriverWait(driver, 25)

    try:
        for b in driver.find_elements(By.XPATH,
                "//button[contains(text(),'Descartar')]"):
            if b.is_displayed():
                driver.execute_script("arguments[0].click();", b)
                time.sleep(1)
    except Exception:
        pass

    if not _copiar_imagem_clipboard(foto_path):
        return False
    time.sleep(0.5)

    try:
        campo = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, SELETOR_CAMPO)))
        driver.execute_script("arguments[0].focus(); arguments[0].click();", campo)
    except Exception as e:
        log.error("campo.foco_falhou", erro=str(e))
        return False
    time.sleep(0.5)

    # Cola imagem (3 tentativas com foco forçado via Win32)
    encontrou_preview = False
    for tentativa_cola in range(3):
        _forcar_foco_chrome()
        try:
            driver.switch_to.window(driver.current_window_handle)
        except Exception:
            pass
        try:
            campo = driver.find_element(By.CSS_SELECTOR, SELETOR_CAMPO)
            campo.click()
            time.sleep(0.3)
        except Exception:
            pass

        pyautogui.hotkey('ctrl', 'v')

        for _ in range(16):
            time.sleep(0.5)
            if driver.find_elements(By.CSS_SELECTOR, SELETOR_BOTAO_ENVIAR):
                encontrou_preview = True
                break

        if encontrou_preview:
            log.debug("imagem.colou", tentativa=tentativa_cola + 1)
            break

        log.warning("imagem.cola_falhou", tentativa=tentativa_cola + 1)
        if tentativa_cola < 2:
            _copiar_imagem_clipboard(foto_path)
            time.sleep(0.5)

    if not encontrou_preview:
        log.error("imagem.cola_3x_falhou")
        _abortar_upload(driver)
        return False

    time.sleep(0.8)

    pyperclip.copy(texto)
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(1.5)

    return _clicar_enviar(driver)


def _enviar_post_so_texto(driver: webdriver.Chrome, texto: str) -> bool:
    """Envia só texto no grupo aberto."""
    wait = WebDriverWait(driver, 25)

    try:
        campo = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, SELETOR_CAMPO)))
        driver.execute_script("arguments[0].focus(); arguments[0].click();", campo)
    except Exception as e:
        log.error("campo.foco_falhou", erro=str(e))
        return False

    time.sleep(0.5)
    _forcar_foco_chrome()

    pyperclip.copy(texto)
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(1)

    pyautogui.press('enter')
    time.sleep(2)

    try:
        campo = driver.find_element(By.CSS_SELECTOR, SELETOR_CAMPO)
        if campo.text.strip() == "":
            return True
    except Exception:
        pass

    log.warning("texto.envio_incerto")
    return True


def _clicar_enviar(driver: webdriver.Chrome) -> bool:
    """3 estratégias + Enter como fallback."""
    seletores = [
        SELETOR_BOTAO_ENVIAR,
        "span[data-icon='send']",
        "span[data-icon='wds-ic-send-filled']",
        "button[aria-label*='Enviar']",
        "button[aria-label*='Send']",
        "div[role='button'][aria-label*='Enviar']",
    ]

    for tentativa in range(3):
        botao_clicado = False
        for sel in seletores:
            try:
                for b in driver.find_elements(By.CSS_SELECTOR, sel):
                    if not b.is_displayed():
                        continue
                    try:
                        b.click()
                        botao_clicado = True
                        break
                    except Exception:
                        pass
                    try:
                        ActionChains(driver).move_to_element(b).click().perform()
                        botao_clicado = True
                        break
                    except Exception:
                        pass
                    try:
                        driver.execute_script("arguments[0].click();", b)
                        botao_clicado = True
                        break
                    except Exception:
                        pass
                if botao_clicado:
                    break
            except Exception:
                continue

        if botao_clicado:
            time.sleep(3)
            try:
                ainda_tem = driver.find_elements(By.CSS_SELECTOR, SELETOR_BOTAO_ENVIAR)
                if not ainda_tem:
                    return True
            except Exception:
                return True

        if tentativa < 2:
            try:
                pyautogui.press('enter')
                time.sleep(2)
                ainda_tem = driver.find_elements(By.CSS_SELECTOR, SELETOR_BOTAO_ENVIAR)
                if not ainda_tem:
                    return True
            except Exception:
                pass

    log.error("envio.falhou_3x")
    _abortar_upload(driver)
    return False


def _abortar_upload(driver: webdriver.Chrome) -> None:
    """Limpa preview travado."""
    log.info("limpando_upload")
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.6)
    except Exception:
        pass
    try:
        for b in driver.find_elements(By.XPATH,
                "//button[contains(text(),'Descartar') or contains(text(),'Discard')]"):
            if b.is_displayed():
                driver.execute_script("arguments[0].click();", b)
                time.sleep(1)
                break
    except Exception:
        pass
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.5)
    except Exception:
        pass


# ============================================================
# Win32 — foco real
# ============================================================

def _forcar_foco_chrome() -> bool:
    """Força foco na janela do Chrome via Win32 + AttachThreadInput."""
    if not HAS_WIN32:
        return False

    try:
        hwnd_chrome = None

        def _enum_callback(hwnd, _):
            nonlocal hwnd_chrome
            if not win32gui.IsWindowVisible(hwnd):
                return True
            titulo = win32gui.GetWindowText(hwnd)
            if "Google Chrome" in titulo or "WhatsApp" in titulo:
                hwnd_chrome = hwnd
                return False
            return True

        win32gui.EnumWindows(_enum_callback, None)

        if not hwnd_chrome:
            return False

        if win32gui.IsIconic(hwnd_chrome):
            win32gui.ShowWindow(hwnd_chrome, win32con.SW_RESTORE)
            time.sleep(0.2)

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        thread_atual = kernel32.GetCurrentThreadId()
        hwnd_fg = user32.GetForegroundWindow()
        thread_fg = user32.GetWindowThreadProcessId(hwnd_fg, None)

        if thread_atual != thread_fg:
            user32.AttachThreadInput(thread_atual, thread_fg, True)
            try:
                user32.SetForegroundWindow(hwnd_chrome)
                user32.BringWindowToTop(hwnd_chrome)
                user32.SetFocus(hwnd_chrome)
            finally:
                user32.AttachThreadInput(thread_atual, thread_fg, False)
        else:
            user32.SetForegroundWindow(hwnd_chrome)

        time.sleep(0.3)
        return True
    except Exception as e:
        log.debug("foco.erro", erro=str(e))
        return False


# ============================================================
# Imagem
# ============================================================

def _copiar_imagem_clipboard(caminho_foto: str) -> bool:
    """Copia imagem pro clipboard como CF_DIB (BMP)."""
    if not HAS_WIN32:
        log.error("clipboard.sem_win32")
        return False
    try:
        img = Image.open(caminho_foto)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "BMP")
        bmp = buf.getvalue()[14:]  # remove header
        buf.close()
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, bmp)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log.error("clipboard.erro", erro=str(e))
        return False


async def _baixar_imagem(url: str) -> str | None:
    """Baixa URL pra arquivo temporário."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content

        ext = ".jpg"
        ct = resp.headers.get("content-type", "").lower()
        if "png" in ct:
            ext = ".png"
        elif "webp" in ct:
            ext = ".webp"

        fd, path = tempfile.mkstemp(suffix=ext, prefix="achadinhos_")
        with os.fdopen(fd, "wb") as f:
            f.write(content)

        log.debug("imagem.baixada", url=url, tamanho=len(content))
        return path
    except Exception as e:
        log.error("imagem.download_falhou", url=url, erro=str(e))
        return None


def _erro(codigo: str, *, tentar_de_novo: bool, detalhes: str = "") -> dict:
    """Resposta de erro padrão."""
    return {
        "ok": False,
        "erro": codigo,
        "tentar_de_novo": tentar_de_novo,
        "detalhes": detalhes,
    }
