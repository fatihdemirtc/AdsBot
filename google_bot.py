"""
Chrome aç -> Google ara -> sonuçlara tıkla -> mouse ile gez -> çık.
Hem komut satırından hem GUI panelinden (panel.py) çağrılabilir.

CLI:   python google_bot.py "arama kelimesi"
GUI:   run_bot(...) fonksiyonunu panel.py kullanır.
"""

import os
import sys
import time
import random
import shutil
import tempfile
import subprocess

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    import undetected_chromedriver as uc
except Exception:
    uc = None


def _chrome_major():
    """Kurulu Chrome ana sürüm no'sunu bul (uc için gerekli). Bulunamazsa None."""
    # 1) Registry (en güvenilir, her PC)
    try:
        import winreg
        for kok in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                k = winreg.OpenKey(kok, r"Software\Google\Chrome\BLBeacon")
                v, _ = winreg.QueryValueEx(k, "version")
                winreg.CloseKey(k)
                return int(v.split(".")[0])
            except Exception:
                continue
    except Exception:
        pass
    # 2) Application klasöründeki sürüm klasörü adı
    for taban in (r"C:\Program Files\Google\Chrome\Application",
                  r"C:\Program Files (x86)\Google\Chrome\Application"):
        try:
            for ad in os.listdir(taban):
                if ad[:2].isdigit() and "." in ad:
                    return int(ad.split(".")[0])
        except Exception:
            continue
    return None

def _temel_klasor():
    """exe yanı (frozen) ya da script klasörü. Her PC'de çalışır."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


MASAUSTU = _temel_klasor()


# Tarayıcı parmak izini insanlaştırır: webdriver/plugins/languages/chrome/WebGL/permissions.
# Her yeni dokümandan ÖNCE çalışır (CDP addScriptToEvaluateOnNewDocument).
STEALTH_JS = r"""
(() => {
  // 1) navigator.webdriver -> yok
  try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch (e) {}

  // 2) languages (tr öncelikli, gerçekçi sıra)
  try {
    Object.defineProperty(navigator, 'languages',
      {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
  } catch (e) {}

  // 3) plugins / mimeTypes — boş dizi = headless işareti. Sahte dolu liste ver.
  try {
    const sahte = [
      {name: 'PDF Viewer', filename: 'internal-pdf-viewer'},
      {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer'},
      {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer'},
      {name: 'Native Client', filename: 'internal-nacl-plugin'},
    ];
    Object.defineProperty(navigator, 'plugins', {
      get: () => {
        const arr = sahte.map(p => Object.assign(Object.create(Plugin.prototype), p));
        arr.item = i => arr[i];
        arr.namedItem = n => arr.find(p => p.name === n) || null;
        return arr;
      }
    });
  } catch (e) {}

  // 4) window.chrome.runtime — gerçek Chrome'da var, otomasyonda yok
  try {
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) window.chrome.runtime = {};
    window.chrome.app = window.chrome.app || {isInstalled: false,
      InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'},
      RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'}};
    window.chrome.csi = window.chrome.csi || function () {};
    window.chrome.loadTimes = window.chrome.loadTimes || function () {};
  } catch (e) {}

  // 5) permissions.query — Notification 'denied'/'default' tutarlılığı
  try {
    const orj = window.navigator.permissions.query;
    window.navigator.permissions.query = (p) =>
      p && p.name === 'notifications'
        ? Promise.resolve({state: Notification.permission})
        : orj(p);
  } catch (e) {}

  // 6) WebGL vendor/renderer — gerçek GPU dizesi (SwiftShader headless'i ele verir)
  try {
    const yama = (proto) => {
      const orj = proto.getParameter;
      proto.getParameter = function (p) {
        if (p === 37445) return 'Google Inc. (Intel)';                    // UNMASKED_VENDOR
        if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)'; // UNMASKED_RENDERER
        return orj.call(this, p);
      };
    };
    if (window.WebGLRenderingContext) yama(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) yama(WebGL2RenderingContext.prototype);
  } catch (e) {}

  // 7) hardwareConcurrency / deviceMemory — gerçekçi sabitler
  try { Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8}); } catch (e) {}
  try { Object.defineProperty(navigator, 'deviceMemory', {get: () => 8}); } catch (e) {}

  // 8) Notification.permission 'default' (otomasyonda bazen 'denied')
  try {
    if (window.Notification && Notification.permission === 'denied') {
      Object.defineProperty(Notification, 'permission', {get: () => 'default'});
    }
  } catch (e) {}
})();
"""


def _stealth_uygula(driver):
    """Stealth JS'i hem yeni dokümanlar için kaydet hem mevcut sayfaya enjekte et."""
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS}
        )
    except Exception:
        pass
    try:
        driver.execute_script(STEALTH_JS)
    except Exception:
        pass


def _log(cb, mesaj):
    """cb varsa GUI'ye gönder, yoksa konsola yaz."""
    if cb:
        cb(mesaj)
    else:
        print(mesaj)


def insanca_bekle(a=0.6, b=1.8):
    """İnsan gibi DÜZENSİZ bekleme. Tek tip uniform değil; ağırlıklı karışık dağılım.

    Patternler:
      ~%15 hızlı tepki   : [a*0.4, a*0.9]
      ~%62 normal        : gauss(merkez, yayılım)
      ~%15 okuma/düşünme : [b, b*2.2]
      ~%5  dalgınlık     : [b*2.5, b*4.5]
    Üstüne mikro jitter (gauss) eklenir.
    """
    r = random.random()
    if r < 0.15:
        t = random.uniform(a * 0.4, a * 0.9)
    elif r < 0.77:
        merkez = (a + b) / 2.0
        t = random.gauss(merkez, (b - a) / 4.0)
        t = min(max(t, a * 0.8), b * 1.1)
    elif r < 0.92:
        t = random.uniform(b, b * 2.2)
    else:
        t = random.uniform(b * 2.5, b * 4.5)
    t += random.gauss(0, 0.08)
    time.sleep(max(0.05, t))


def _bezier_nokta(p0, p1, p2, p3, t):
    """Kübik Bezier eğrisinde t (0..1) için (x, y)."""
    u = 1.0 - t
    x = (u ** 3) * p0[0] + 3 * (u ** 2) * t * p1[0] + 3 * u * (t ** 2) * p2[0] + (t ** 3) * p3[0]
    y = (u ** 3) * p0[1] + 3 * (u ** 2) * t * p1[1] + 3 * u * (t ** 2) * p2[1] + (t ** 3) * p3[1]
    return x, y


def _ease(t):
    """Yavaş başla - hızlan - yavaşla (insan ivmesi). smoothstep."""
    return t * t * (3 - 2 * t)


def _fare_kaydir(driver, x, y, adim=None):
    """Fareyi mevcut konumdan (x,y) noktasına KÜBİK BEZIER eğriyle götür (insan gibi).

    Düz çizgi değil: rastgele iki kontrol noktasıyla hafif kavisli yol.
    Adım sayısı mesafeyle orantılı; her adımda easing'li ilerleme + mikro titreşim.
    """
    try:
        eb = driver.execute_script("return [innerWidth, innerHeight];")
        gw, gh = eb[0], eb[1]
        x = max(2, min(int(x), gw - 2))
        y = max(2, min(int(y), gh - 2))

        sx = getattr(driver, "_fare_x", gw // 2)
        sy = getattr(driver, "_fare_y", gh // 2)

        mesafe = ((x - sx) ** 2 + (y - sy) ** 2) ** 0.5
        if adim is None:
            adim = int(min(60, max(12, mesafe / 12)))  # mesafeyle orantılı

        p0 = (sx, sy)
        p3 = (x, y)
        # Kontrol noktaları: başlangıç-bitiş çizgisine dik yönde rastgele sapma
        sapma = max(8, mesafe * random.uniform(0.12, 0.30))
        mx, my = (sx + x) / 2.0, (sy + y) / 2.0
        dx, dy = (x - sx), (y - sy)
        nrm = (dx * dx + dy * dy) ** 0.5 or 1.0
        # dik birim vektör
        px, py = -dy / nrm, dx / nrm
        yon = random.choice((-1, 1))
        p1 = (mx + px * sapma * yon * random.uniform(0.4, 1.0) - dx * 0.15,
              my + py * sapma * yon * random.uniform(0.4, 1.0) - dy * 0.15)
        p2 = (mx + px * sapma * yon * random.uniform(0.2, 0.8) + dx * 0.15,
              my + py * sapma * yon * random.uniform(0.2, 0.8) + dy * 0.15)

        for k in range(1, adim + 1):
            t = _ease(k / adim)
            bx, by = _bezier_nokta(p0, p1, p2, p3, t)
            jx = bx + random.uniform(-1.5, 1.5)
            jy = by + random.uniform(-1.5, 1.5)
            driver.execute_script(
                """
                var ev = new MouseEvent('mousemove',
                  {clientX: arguments[0], clientY: arguments[1], bubbles: true});
                var el = document.elementFromPoint(arguments[0], arguments[1]);
                (el || document.body).dispatchEvent(ev);
                """,
                jx, jy,
            )
            # hız değişken: ortada hızlı, uçlarda yavaş
            hiz = 0.003 + (1 - abs(0.5 - k / adim) * 2) * 0.010
            time.sleep(hiz * random.uniform(0.7, 1.4))
        driver._fare_x, driver._fare_y = x, y
    except Exception:
        pass


def mouse_gezin(driver, dongu=3):
    """Sayfada fareyle gez: aşağı kaydır + viewport'ta rastgele noktalara süzül + öğe üstünde dur."""
    actions = ActionChains(driver)
    for _ in range(dongu):
        eb = driver.execute_script("return [innerWidth, innerHeight];")
        gw, gh = eb[0], eb[1]

        # 1) viewport üstünde rastgele 2-4 noktada gez
        for _ in range(random.randint(2, 4)):
            _fare_kaydir(driver, random.randint(40, gw - 40), random.randint(40, gh - 40))
            time.sleep(random.uniform(0.15, 0.5))

        # 2) görünür bir öğenin üstüne gerçek fareyle hover yap
        try:
            ogeler = driver.find_elements(By.CSS_SELECTOR, "a, h1, h2, h3, p, img, button")
            ogeler = [o for o in ogeler if o.is_displayed()]
            if ogeler:
                hedef = random.choice(ogeler[:30])
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", hedef
                )
                time.sleep(random.uniform(0.3, 0.7))
                actions.move_to_element(hedef).perform()
        except Exception:
            pass

        # 3) yumuşak aşağı kaydır
        driver.execute_script(f"window.scrollBy({{top: {random.randint(250, 650)}, behavior:'smooth'}});")
        insanca_bekle(0.5, 1.2)


def _tum_sayfayi_kaydir(driver, max_adim=30):
    """Sayfayı parça parça EN ALTA kadar indir (lazy sonuç + alt reklamlar yüklensin), sonra başa dön.

    Aşağıdaki organik sonuçlar ve #bottomads reklamları ancak scroll'la DOM'a gelir.
    NOT: 'behavior:smooth' KULLANILMAZ — animasyon bitmeden scrollY okununca
    erken 'dibe vardı' sanılıp duruyordu. Anlık scrollBy + bekleme ile ölç.
    """
    try:
        son_y = -1
        durgun = 0
        for _ in range(max_adim):
            driver.execute_script("window.scrollBy(0, arguments[0]);",
                                  random.randint(350, 650))
            insanca_bekle(0.35, 0.8)   # lazy içerik yüklensin
            y = driver.execute_script(
                "return Math.round(window.scrollY + window.innerHeight);")
            toplam = driver.execute_script("return document.body.scrollHeight;")
            if y >= toplam - 4:        # gerçekten dibe vardı
                break
            if abs(y - son_y) < 4:     # konum değişmiyor (büyümüyor)
                durgun += 1
                if durgun >= 2:        # üst üste 2 kez takıldıysa bitir
                    break
            else:
                durgun = 0
            son_y = y
        insanca_bekle(0.4, 0.9)
        driver.execute_script("window.scrollTo(0, 0);")   # başa anlık dön
        insanca_bekle(0.6, 1.1)
    except Exception:
        pass


# QWERTY komşuluk — typo simülasyonu için (yanlış tuş = komşu tuş)
_KOMSU = {
    "q": "wa", "w": "qeas", "e": "wrds", "r": "etdf", "t": "ryfg",
    "y": "tugh", "u": "yihj", "i": "uojk", "o": "ipkl", "p": "ol",
    "a": "qwsz", "s": "awedxz", "d": "serfcx", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "j": "huikmn", "k": "jiolm", "l": "kop",
    "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn",
    "n": "bhjm", "m": "njk",
}


def _insanca_yaz(el, metin, hata_olasi=0.06):
    """Tuş tuş, DEĞİŞKEN hızla yaz. Boşlukta dur, ara sıra düşün, ara sıra typo yapıp düzelt."""
    for harf in metin:
        # boşluk öncesi/sonrası küçük duraklama (kelime sınırı)
        if harf == " ":
            time.sleep(random.uniform(0.12, 0.35))

        # typo: komşu tuşa bas -> fark et -> backspace -> doğrusu
        kk = harf.lower()
        if kk in _KOMSU and random.random() < hata_olasi:
            try:
                el.send_keys(random.choice(_KOMSU[kk]))
                time.sleep(random.uniform(0.12, 0.45))   # fark etme gecikmesi
                el.send_keys(Keys.BACKSPACE)
                time.sleep(random.uniform(0.08, 0.22))
            except Exception:
                pass

        try:
            el.send_keys(harf)
        except Exception:
            pass

        # karakter başı değişken gecikme (gauss); noktalama sonrası daha uzun
        d = random.gauss(0.11, 0.05)
        if harf in ",.?!-":
            d += random.uniform(0.05, 0.20)
        time.sleep(max(0.03, d))

        # ara sıra "ne yazsam" düşünme molası
        if random.random() < 0.04:
            time.sleep(random.uniform(0.4, 1.1))


def cerez_kapat(driver):
    """Google çerez/onay penceresini kabul et (varsa)."""
    xpaths = [
        "//button[contains(., 'Tümünü kabul et')]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(., 'Kabul et')]",
        "//div[@role='none']//button[2]",
    ]
    # Çerez penceresi genelde hiç çıkmaz (ncr) -> uzun beklememek için kısa timeout.
    # İlk xpath'te 1.2 sn dene; çıkmadıysa kalanları anında (0 bekleme) kontrol et.
    for i, xp in enumerate(xpaths):
        try:
            btn = WebDriverWait(driver, 1.2 if i == 0 else 0).until(
                EC.element_to_be_clickable((By.XPATH, xp))
            )
            btn.click()
            insanca_bekle(0.3, 0.7)
            return True
        except Exception:
            continue
    return False


def konum_popup_kapat(driver):
    """Google 'konumunuzu kullanmak istiyor' / izin penceresini REDDEDEREK kapat.

    Özellikle mobilde çıkar ve sayfayı bloklar -> hedeflere tıklamayı engeller.
    Negatif (reddet/daha sonra) butonu hangi metinle çıkarsa tıkla.
    """
    metinler = [
        "Hayır teşekkürler", "Daha sonra", "Şimdi değil", "Konumu kullanma",
        "Bu sitede izin verme", "İzin verme", "Reddet", "Vazgeç", "Kapat",
        "No thanks", "Not now", "Never", "Don't allow", "Block", "Dismiss",
    ]
    try:
        for m in metinler:
            try:
                btns = driver.find_elements(
                    By.XPATH,
                    f"//button[contains(normalize-space(.), '{m}')]"
                    f" | //*[@role='button'][contains(normalize-space(.), '{m}')]"
                    f" | //div[@role='button'][contains(normalize-space(.), '{m}')]"
                    f" | //g-raised-button[contains(normalize-space(.), '{m}')]"
                    f" | //a[contains(normalize-space(.), '{m}')]")
                for b in btns:
                    try:
                        if b.is_displayed():
                            try:
                                b.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", b)
                            insanca_bekle(0.2, 0.5)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    return False


def sonuc_bekle(driver, sn=20):
    """Sonuç sayfasını bekle: #search VEYA #rso VEYA h3 linkleri."""
    return WebDriverWait(driver, sn).until(
        lambda d: d.find_elements(By.ID, "search")
        or d.find_elements(By.ID, "rso")
        or d.find_elements(By.CSS_SELECTOR, "a h3")
    )


def _temiz_domain(domain):
    return (domain.strip().lower()
            .replace("https://", "").replace("http://", "")
            .replace("www.", "").strip("/"))


def _href_host(href):
    """href'in gerçek HEDEF host'unu çıkar (yol/sorgu değil).

    Google yönlendirmesi (/url?q=) ve reklam (/aclk?...&adurl=) ise asıl hedefi
    q/adurl/url parametresinden alır. Döner: temiz host (www'siz) ya da "".
    """
    import urllib.parse as up
    try:
        p = up.urlparse(href or "")
        host = p.netloc
        yol = (p.path or "")
        # yönlendirme: gerçek hedef parametrede
        if (not host) or ("google." in host) or "/aclk" in yol or yol == "/url" or "/pagead/" in yol:
            par = up.parse_qs(p.query)
            for anahtar in ("adurl", "url", "q", "u"):
                if par.get(anahtar):
                    host = up.urlparse(par[anahtar][0]).netloc or host
                    break
        return _temiz_domain(host)
    except Exception:
        return ""


def _host_es(href, domain):
    """href'in HOST'u domain ile eşleşiyor mu? SADECE alan adı; tam URL değil.

    Eşleşme: host == domain  ya da  host, '.'+domain ile biter (alt alan adı).
    """
    domain = _temiz_domain(domain)
    if not domain:
        return False
    host = _href_host(href)
    if not host:
        return False
    return host == domain or host.endswith("." + domain)


def _gorunur_hostlar(driver, limit=15):
    """Organik sonuçların host listesini döndür (teşhis/log için). Mobil + masaüstü.

    Mobilde 'a h3' boş kalabilir -> birden çok seçici dener.
    """
    hostlar = []
    seciciler = ("a h3", "#rso a[href]", "#search a[href]",
                 "div[data-hveid] a[href]", "a[href]")
    try:
        for sel in seciciler:
            try:
                if sel == "a h3":
                    elems = []
                    for h3 in driver.find_elements(By.CSS_SELECTOR, "a h3"):
                        try:
                            elems.append(h3.find_element(By.XPATH, "./ancestor::a[1]"))
                        except Exception:
                            continue
                else:
                    elems = driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                elems = []
            for a in elems:
                try:
                    h = _href_host(a.get_attribute("href") or "")
                    if h and "google" not in h and h not in hostlar:
                        hostlar.append(h)
                except Exception:
                    continue
            if len(hostlar) >= 3:   # yeterli örnek toplandı
                break
    except Exception:
        pass
    return hostlar[:limit]


def _hedef_link_bul(driver, domain):
    """
    Sonuçlarda href'i domain içeren görünür linki döndür (yoksa None).
    Önce başlıklı (h3) organik sonucu tercih eder, yoksa herhangi bir linki.
    """
    domain = _temiz_domain(domain)
    if not domain:
        return None

    def _eslesir(a):
        try:
            return a.is_displayed() and _host_es(a.get_attribute("href") or "", domain)
        except Exception:
            return False

    # 1) Başlıklı organik sonuç (a > h3) — en doğru tıklama hedefi
    try:
        for h3 in driver.find_elements(By.CSS_SELECTOR, "a h3"):
            try:
                a = h3.find_element(By.XPATH, "./ancestor::a[1]")
                if _eslesir(a):
                    return a
            except Exception:
                continue
    except Exception:
        pass

    # 2) Yedek: sayfadaki tüm linkler (konteyner fark etmeksizin)
    for sec in ("#search a, #rso a, #center_col a", "a[href]"):
        for a in driver.find_elements(By.CSS_SELECTOR, sec):
            if _eslesir(a):
                return a
    return None


# Google reklam (Ad/Sponsorlu) bloğu seçicileri
REKLAM_SECICILER = (
    "#tads a, #tadsb a, #bottomads a, #taw a, "
    "div[data-text-ad] a, div[aria-label='Reklamlar'] a, "
    "div[aria-label='Ads'] a, [data-pcu] a"
)

# Reklam tıklaması href'inde bu izler bulunur (konteyner fark etmez)
REKLAM_HREF_IZ = ("/aclk?", "/aclk%3f", "googleadservices.com",
                  "googlesyndication.com", "/pagead/", "&adurl=", "?adurl=")


def _reklam_mi(href):
    h = (href or "").lower()
    return any(iz in h for iz in REKLAM_HREF_IZ)


def _reklam_linkleri(driver):
    """Sayfadaki TÜM reklam linklerini döndür (görünür, tekrarsız).

    Tespit JS ile yapılır -> Google'ın sık değişen reklam DOM'una dayanıklı:
      - reklam konteyneri içinde mi (#tads/#tadsb/#bottomads/#taw, data-text-ad/pcu/rw, aria-label)
      - 'Sponsorlu/Sponsored/Reklam' etiketi yakınında mı
      - href reklam izi taşıyor mu (aclk/adservices/pagead/adurl)
    """
    js = r"""
    const out = [], seen = new Set();
    const ID_AD = ['tads','tadsb','bottomads','taw'];
    const reklamKonteyner = (el) => {
      let p = el;
      for (let i = 0; i < 7 && p; i++, p = p.parentElement) {
        if (p.id && ID_AD.includes(p.id)) return true;
        if (p.hasAttribute && (p.hasAttribute('data-text-ad') ||
            p.hasAttribute('data-pcu') || p.hasAttribute('data-rw'))) return true;
        const al = (p.getAttribute && (p.getAttribute('aria-label') || '')) || '';
        if (/reklam|^ads$/i.test(al)) return true;
      }
      return false;
    };
    const sponEtiket = (el) => {
      let p = el;
      for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
        const t = ((p.innerText || '').slice(0, 40)).toLowerCase();
        if (/^sponsorlu|^sponsored|^reklam\b|·\s*sponsorlu|·\s*sponsored/.test(t)) return true;
      }
      return false;
    };
    const hrefAd = (h) => /\/aclk|googleadservices|googlesyndication|\/pagead\/|adurl=/.test((h||'').toLowerCase());
    document.querySelectorAll('a[href]').forEach(a => {
      const href = a.href || '';
      if (!href || seen.has(href)) return;
      if (hrefAd(href) || reklamKonteyner(a) || sponEtiket(a)) {
        const r = a.getBoundingClientRect();
        if (a.offsetParent !== null || r.width > 0 || r.height > 0) {
          seen.add(href);
          out.push(a);
        }
      }
    });
    return out;
    """
    try:
        return driver.execute_script(js) or []
    except Exception:
        return []


def _icinde_reklam_konteyner(a):
    """Link bir reklam konteyneri içinde mi (id/aria ile)."""
    try:
        return a.find_element(
            By.XPATH,
            "./ancestor::*[@id='tads' or @id='tadsb' or @id='bottomads'"
            " or @data-text-ad or @aria-label='Reklamlar'"
            " or @aria-label='Ads'][1]"
        ) is not None
    except Exception:
        return False


def _reklam_link_bul(driver, domain):
    """Reklamlar arasında HOST'u domain ile eşleşen ilk görünür linki döndür (tam URL değil)."""
    domain = _temiz_domain(domain)
    if not domain:
        return None
    for a in _reklam_linkleri(driver):
        try:
            if _host_es(a.get_attribute("href") or "", domain):
                return a
        except Exception:
            continue
    return None


def _reklam_domain(href):
    """Reklam aclk/adurl href'inden gerçek hedef domaini çıkar (loglamak için)."""
    import urllib.parse as up
    h = href or ""
    try:
        # adurl=... parametresi varsa onu çöz
        q = up.urlparse(h).query
        par = up.parse_qs(q)
        for anahtar in ("adurl", "url", "q"):
            if anahtar in par and par[anahtar]:
                return _temiz_domain(up.urlparse(par[anahtar][0]).netloc
                                     or par[anahtar][0])
    except Exception:
        pass
    try:
        return _temiz_domain(up.urlparse(h).netloc)
    except Exception:
        return h[:40]


def _ziyaret_href(driver, href, etiket, log_cb, gez_dongu=5):
    """
    URL'i YENİ SEKMEDE aç, sitede gez, sekmeyi kapat, SERP penceresine dön.
    SERP penceresi hep açık kalır -> stale/back/pencere-kapandı sorunu olmaz.
    """
    _log(log_cb, f"  -> giriliyor: {etiket}")
    ana = driver.current_window_handle
    try:
        driver.switch_to.new_window("tab")
        driver.get(href)
        insanca_bekle(1.5, 3.0)
        mouse_gezin(driver, dongu=gez_dongu)   # sitede gez
    except Exception as ex:
        _log(log_cb, f"  ! '{etiket}' ziyaret hatası: {str(ex)[:70]}")
    finally:
        # açtığımız sekmeyi kapat, ana (SERP) pencereye dön
        try:
            if driver.current_window_handle != ana:
                driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(ana)
        except Exception:
            # ana kapandıysa kalan ilk pencereye geç
            if driver.window_handles:
                driver.switch_to.window(driver.window_handles[0])
    insanca_bekle()


def _siteyi_gez(driver, hedef, log_cb, etiket, serp_url=None, gez_dongu=5):
    """
    Verilen link öğesine tıkla, sitede gez, sonra SERP'e KESİN geri dön.
    Yeni sekmede açılırsa o sekmeyi yönetir. back() yerine serp_url'e gider.
    """
    _log(log_cb, f"  -> tıklanıyor: {etiket}")
    ana_pencere = driver.current_window_handle
    onceki_handles = set(driver.window_handles)

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", hedef)
    ActionChains(driver).move_to_element(hedef).perform()
    insanca_bekle()
    try:
        hedef.click()
    except Exception:
        driver.execute_script("arguments[0].click();", hedef)

    insanca_bekle(1.5, 3.0)

    # yeni sekme açıldı mı?
    yeni = set(driver.window_handles) - onceki_handles
    if yeni:
        driver.switch_to.window(yeni.pop())
        mouse_gezin(driver, dongu=gez_dongu)   # sitede gez
        driver.close()                          # sekmeyi kapat
        driver.switch_to.window(ana_pencere)
    else:
        mouse_gezin(driver, dongu=gez_dongu)   # aynı sekmede gez
        # SERP'e kesin dön
        if serp_url:
            driver.get(serp_url)
        else:
            driver.back()

    sonuc_bekle(driver, 20)
    insanca_bekle()


def _yanlis_tikla_don(driver, serp_url, log_cb, kacin=None):
    """İnsan gibi: ilgisiz bir organik sonuca 'yanlışlıkla' tıkla, kısa gez, SERP'e dön.

    kacin: tıklanmaması gereken domainler (gerçek hedefler) + reklamlar atlanır.
    """
    try:
        kacin = [_temiz_domain(d) for d in (kacin or [])]
        adaylar = []
        for h3 in driver.find_elements(By.CSS_SELECTOR, "a h3"):
            try:
                if not h3.is_displayed():
                    continue
                a = h3.find_element(By.XPATH, "./ancestor::a[1]")
                href = (a.get_attribute("href") or "").lower()
                if not href or _reklam_mi(href):
                    continue
                if any(d and d in href for d in kacin):
                    continue
                adaylar.append((a, h3.text[:50]))
            except Exception:
                continue
        if not adaylar:
            return
        a, etiket = random.choice(adaylar[:6])
        _log(log_cb, f"  ~ yanlış tık (insan davranışı): {etiket}")
        _siteyi_gez(driver, a, log_cb, f"[yanlış] {etiket}", serp_url,
                    gez_dongu=random.randint(2, 3))
    except Exception:
        pass


def _reklam_domainleri_topla(driver, log_cb):
    """SERP'teki TÜM reklam domainlerini çıkar ve LOGLA (listeye ekleme YOK)."""
    bulunan = []
    try:
        for a in _reklam_linkleri(driver):
            try:
                rd = _temiz_domain(_reklam_domain(a.get_attribute("href") or ""))
            except Exception:
                rd = ""
            if rd and rd not in bulunan:
                bulunan.append(rd)
    except Exception:
        pass
    if bulunan:
        _log(log_cb, f"  Reklam domainleri ({len(bulunan)}): {', '.join(bulunan)}")
    else:
        _log(log_cb, "  Sayfada reklam linki bulunamadı.")
    return bulunan


def _ag_bekle_ve_ac(driver, url, log_cb, iptal_mi=None, deneme=5):
    """URL'i aç; internet yoksa (ERR_INTERNET_DISCONNECTED vb.) ağ gelene kadar bekle-tekrar dene.

    Özellikle gerçek telefon + uçak modu IP yenileme sonrası ağ geç gelirse işe yarar.
    """
    ag_hatalari = ("ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED",
                   "ERR_NAME_NOT_RESOLVED", "ERR_PROXY_CONNECTION_FAILED",
                   "ERR_CONNECTION_RESET", "ERR_ADDRESS_UNREACHABLE")
    for i in range(deneme):
        if iptal_mi and iptal_mi():
            return
        try:
            driver.get(url)
            return
        except Exception as ex:
            m = str(ex)
            if any(h in m for h in ag_hatalari):
                bekle = 4 + i * 3
                _log(log_cb, f"  ! İnternet yok, {bekle} sn bekle, tekrar dene "
                             f"({i + 1}/{deneme})...")
                time.sleep(bekle)
                continue
            raise
    # son deneme: başarısızsa hatayı yükselt
    driver.get(url)


def _mobil_emulasyon():
    """Chrome mobil cihaz emülasyonu sözlüğü (Pixel 7 benzeri, mobil UA)."""
    major = _chrome_major() or 124
    ua = (f"Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
          f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Mobile Safari/537.36")
    return {
        "deviceMetrics": {"width": 412, "height": 915, "pixelRatio": 2.625, "mobile": True},
        "userAgent": ua,
    }


def _reklamlari_sirayla_isle(driver, domainler, log_cb, serp_url, iptal_mi,
                             gez_dongu=5):
    """Reklamları YUKARIDAN AŞAĞIYA sırayla gez; her birinin domaini listede mi bak.

    Akış: kaydır -> ilk reklamı bul -> listede mi? varsa GİR (tıkla-gez-dön), yoksa atla
          -> sıradaki işlenmemiş reklama geç -> tüm reklamlar bitene kadar tekrar.
    SERP'e dönünce DOM değişir; işlenenler domain ile takip edilir (tekrar girilmez).
    """
    liste = set(_temiz_domain(d) for d in domainler)
    islenmis = set()      # girilen ya da atlanan reklam domainleri
    girilen = 0
    while True:
        if iptal_mi():
            _log(log_cb, "İptal edildi.")
            break
        konum_popup_kapat(driver)
        _tum_sayfayi_kaydir(driver)        # tüm reklamlar (üst+alt) yüklensin

        # sayfadaki reklamları DOM sırasıyla (üstten alta) domainleriyle al, tekrarsız
        sirali = []
        gorulen = set()
        for a in _reklam_linkleri(driver):
            try:
                rd = _temiz_domain(_reklam_domain(a.get_attribute("href") or ""))
            except Exception:
                rd = ""
            if rd and rd not in gorulen:
                gorulen.add(rd)
                sirali.append(rd)

        # ilk İŞLENMEMİŞ reklamı seç (sıradaki)
        rd = next((d for d in sirali if d not in islenmis), None)
        if rd is None:
            break                          # tüm reklamlar işlendi

        islenmis.add(rd)
        if rd in liste:
            hedef = _reklam_link_bul(driver, rd)
            if hedef:
                _log(log_cb, f"  ✓ reklam listende VAR: {rd} -> giriliyor")
                _siteyi_gez(driver, hedef, log_cb, f"[Ad] {rd}", serp_url,
                            gez_dongu=gez_dongu)
                girilen += 1
            else:
                _log(log_cb, f"  ! '{rd}' reklamı tekrar bulunamadı, atlandı.")
        else:
            _log(log_cb, f"  – reklam listende yok: {rd} -> atlandı")

    _log(log_cb, f"  Reklam tarama bitti: {girilen} listedeki reklama girildi, "
                 f"{len(islenmis)} reklam kontrol edildi.")
    return girilen


def run_bot(arama, hedef_site="", tiklama=3, detach=False, gorunmez=False,
            sadece_reklam=False, log_cb=None, dur_kontrol=None, mobil=False,
            gercek_telefon=False, cihaz_seri=None):
    """
    Tek bir arama çalıştır.
      arama         : aranacak kelime (str)
      hedef_site    : tıklanacak site(ler). Virgülle birden çok domain.
                      Boşsa ilk 'tiklama' kadar sonuca tıklar.
      tiklama       : hedef site yoksa kaç sonuca tıklanacağı (int)
      detach        : True ise python kapanınca tarayıcı açık kalır
      gorunmez      : True ise headless (arka planda) çalışır
      sadece_reklam : True ise SADECE reklam (Ad/Sponsorlu) linklerine tıklar
      log_cb        : log mesajı için callback fn(str)
      dur_kontrol   : iptal için fn() -> True dönerse durur
      mobil         : True ise Chrome'u telefon emülasyonunda açar (dar viewport + mobil UA)
      gercek_telefon: True ise ADB ile bağlı GERÇEK Android telefondaki Chrome'u sürer
                      (androidPackage). Gerçek mobil fingerprint + IP. uc/emülasyon kullanılmaz.
      cihaz_seri    : gercek_telefon için hedef cihaz serisi (birden çok cihaz varsa)
    """

    def iptal_mi():
        return dur_kontrol() if dur_kontrol else False

    # Her çalışmada benzersiz profil -> kilit/çakışma yok (DevToolsActivePort hatası)
    profil = tempfile.mkdtemp(prefix="selenium_chrome_")

    def _ortak_arg(op):
        op.add_argument(f"--user-data-dir={profil}")
        op.add_argument("--no-first-run")
        op.add_argument("--no-default-browser-check")
        op.add_argument("--lang=tr-TR")
        # NOT: --no-sandbox KALDIRILDI. Gerçek kullanıcıda yoktur -> bot sinyali.
        # Eğer çökerse (root/Docker/eski sistem) geri ekle.
        op.add_argument("--disable-dev-shm-usage")
        if mobil:
            op.add_argument("--window-size=412,915")
        elif gorunmez:
            op.add_argument("--window-size=1920,1080")

    driver = None
    if gercek_telefon:
        # --- GERÇEK telefon: ADB ile bağlı cihazdaki Chrome'u sür (androidPackage) ---
        # chromedriver, cihazın Chrome sürümüne uygun olmalı. Selenium Manager indirir.
        # chromedriver adb'yi PATH'ten bulur -> adb klasörünü PATH'e ekle.
        try:
            adb_dir = os.path.dirname(adb_bul())
            if adb_dir and adb_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = adb_dir + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass
        op = webdriver.ChromeOptions()
        op.add_experimental_option("androidPackage", "com.android.chrome")
        if cihaz_seri:
            op.add_experimental_option("androidDeviceSerial", cihaz_seri)
        op.add_argument("--disable-blink-features=AutomationControlled")
        op.add_experimental_option("excludeSwitches", ["enable-automation"])
        op.add_argument("--lang=tr-TR")
        _log(log_cb, f"Telefon Chrome'u açılıyor (ADB)... "
                     f"cihaz: {cihaz_seri or 'otomatik'}, arama: '{arama}'")
        driver = webdriver.Chrome(options=op)
    elif uc is not None:
        # undetected-chromedriver: Google bot tespitini ciddi azaltır.
        # NOT: uc kendi profilini yönetir -> custom --user-data-dir / --no-sandbox VERME.
        # uc'nin oto sürüm tespiti bozuk olabilir -> major'u biz veriyoruz.
        try:
            op = uc.ChromeOptions()
            op.add_argument("--lang=tr-TR")
            op.add_experimental_option("prefs", {
                "profile.default_content_setting_values.geolocation": 2,  # 2 = blokla
                "profile.default_content_setting_values.notifications": 2,
            })
            if mobil:
                op.add_experimental_option("mobileEmulation", _mobil_emulasyon())
                op.add_argument("--window-size=412,915")
            elif gorunmez:
                op.add_argument("--window-size=1920,1080")
            major = _chrome_major()
            _log(log_cb, f"Chrome (uc) açılıyor... (sürüm {major}"
                         f"{', mobil' if mobil else ''}) arama: '{arama}'")
            driver = uc.Chrome(options=op, headless=gorunmez,
                               use_subprocess=True, version_main=major)
            if mobil:
                try:
                    driver.set_window_size(412, 915)
                except Exception:
                    pass
            else:
                try:
                    driver.maximize_window()
                except Exception:
                    pass
        except Exception as ex:
            _log(log_cb, f"uc başarısız ({str(ex)[:80]}), düz Selenium'a geçiliyor.")
            driver = None

    if driver is None and not gercek_telefon:
        # Yedek: düz Selenium
        op = webdriver.ChromeOptions()
        if not mobil:
            op.add_argument("--start-maximized")
        op.add_argument("--disable-blink-features=AutomationControlled")
        # Botu ele veren switch'leri kapat:
        #  - enable-automation: "otomatik yazılım kontrol ediyor" çubuğu + webdriver=true
        #  - useAutomationExtension: otomasyon eklentisi izi
        op.add_experimental_option("excludeSwitches", ["enable-automation"])
        op.add_experimental_option("useAutomationExtension", False)
        op.add_argument("--disable-infobars")
        op.add_experimental_option("prefs", {
            "profile.default_content_setting_values.geolocation": 2,  # 2 = blokla
            "profile.default_content_setting_values.notifications": 2,
        })
        if mobil:
            op.add_experimental_option("mobileEmulation", _mobil_emulasyon())
        _ortak_arg(op)
        op.add_argument("--disable-gpu")
        op.add_argument("--remote-debugging-port=0")
        if gorunmez:
            op.add_argument("--headless=new")
        op.add_experimental_option("detach", detach)
        _log(log_cb, f"Chrome açılıyor... arama: '{arama}'")
        driver = webdriver.Chrome(options=op)

    if gercek_telefon:
        # Gerçek cihaz: mobil fingerprint'i BOZMA. Sadece webdriver izini gizle.
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"})
        except Exception:
            pass
    else:
        _stealth_uygula(driver)

    # Konum iznini CDP ile REDDET (popup hiç çıkmasın). Her modda denenir.
    for _kok in ("https://www.google.com", "https://www.google.com.tr"):
        try:
            driver.execute_cdp_cmd("Browser.setPermission", {
                "origin": _kok,
                "permission": {"name": "geolocation"},
                "setting": "denied",
            })
        except Exception:
            pass

    try:
        # internet yoksa (uçak modu yeni kapandıysa) bekle-tekrar dene
        _ag_bekle_ve_ac(driver, "https://www.google.com/ncr", log_cb, iptal_mi)
        if iptal_mi():
            return
        # sayfa yükü sonrası KISA sabit bekleme (insanca_bekle'nin uzun kuyruğu yok)
        time.sleep(random.uniform(0.3, 0.7))
        cerez_kapat(driver)
        if iptal_mi():
            return

        kutu = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        kutu.click()
        time.sleep(random.uniform(0.2, 0.5))
        _insanca_yaz(kutu, arama)
        time.sleep(random.uniform(0.25, 0.6))   # yazım sonrası kısa, sonra ara
        kutu.send_keys(Keys.RETURN)
        _log(log_cb, "Arama yapıldı, sonuçlar bekleniyor...")

        try:
            sonuc_bekle(driver, 20)
        except Exception:
            url = driver.current_url
            if "/sorry/" in url or "/recaptcha" in url:
                # Google bot tespiti / CAPTCHA: bu IP yanmış
                if not gorunmez:
                    _log(log_cb, "  ! GOOGLE CAPTCHA. 60 sn elle çöz (görünür mod)...")
                    try:
                        WebDriverWait(driver, 60).until(
                            lambda d: "/sorry/" not in d.current_url
                            and "/recaptcha" not in d.current_url
                        )
                        sonuc_bekle(driver, 20)
                    except Exception:
                        raise RuntimeError(
                            "Google CAPTCHA/engel: IP yanmış. Yeni IP gerek "
                            "(uçak modu / VPN / proxy) ya da bir süre bekle.")
                else:
                    raise RuntimeError(
                        "Google CAPTCHA/engel (headless): IP yanmış. "
                        "Yeni IP gerek (uçak modu / VPN / proxy).")
            else:
                driver.save_screenshot(os.path.join(MASAUSTU, "hata.png"))
                _log(log_cb, f"Sonuç gelmedi. URL: {url}")
                raise

        insanca_bekle()
        # konum/izin popup'ı çıktıysa reddederek kapat (sayfayı bloklamasın)
        if konum_popup_kapat(driver):
            _log(log_cb, "  Konum izni penceresi kapatıldı.")
        serp_url = driver.current_url   # sonuç sayfasına kesin dönmek için
        mouse_gezin(driver, dongu=2)

        domainler = [d.strip() for d in hedef_site.split(",") if d.strip()]

        if sadece_reklam:
            # --- SADECE reklam (Ad/Sponsorlu) linklerini iyice tara ---
            mouse_gezin(driver, dongu=1)
            _tum_sayfayi_kaydir(driver)    # üst+alt tüm reklamlar yüklensin
            rek = _reklam_linkleri(driver)
            rek_domainler = []
            for a in rek:
                try:
                    rek_domainler.append(_reklam_domain(a.get_attribute("href")))
                except Exception:
                    pass
            _log(log_cb, f"  {len(rek)} reklam linki bulundu. "
                         f"Reklam siteleri: {', '.join(sorted(set(d for d in rek_domainler if d))) or '-'}")
            if domainler:
                # Reklamları YUKARIDAN AŞAĞIYA sırayla gez; listende olana gir, olmayanı atla
                _reklamlari_sirayla_isle(driver, domainler, log_cb, serp_url, iptal_mi)
            else:
                # site belirtilmemiş: ilk N reklamı tıkla (back sonrası yeniden çek)
                for i in range(tiklama):
                    if iptal_mi():
                        break
                    rekler = _reklam_linkleri(driver)
                    if i >= len(rekler):
                        break
                    hedef = rekler[i]
                    etiket = _temiz_domain(hedef.get_attribute("href") or "")[:50]
                    _siteyi_gez(driver, hedef, log_cb, f"[Ad] {etiket}", serp_url)
        elif domainler:
            # --- Hedef site(ler): SERP'teki gerçek sonuca TIKLA ---
            # Her domaini SERP'te taze bul, tıkla, gez, SERP'e dön, sıradakine geç.
            mouse_gezin(driver, dongu=1)
            konum_popup_kapat(driver)     # geç çıkan konum penceresini kapat
            _tum_sayfayi_kaydir(driver)   # tüm sonuç + alt reklamlar yüklensin
            # SERP'teki reklam domainlerini sadece logla (listeye ekleme yok)
            _reklam_domainleri_topla(driver, log_cb)
            # teşhis: sayfadaki organik host'lar (hedef tutmazsa neden görülür)
            _log(log_cb, f"  Organik sonuç host'ları: "
                         f"{', '.join(_gorunur_hostlar(driver)) or '-'}")
            # insan gibi: %25 ihtimalle önce ilgisiz bir sonuca tıkla-dön
            if not iptal_mi() and random.random() < 0.25:
                _yanlis_tikla_don(driver, serp_url, log_cb, kacin=domainler)
            tiklanan = 0
            for domain in domainler:
                if iptal_mi():
                    _log(log_cb, "İptal edildi.")
                    break
                # 1) organik sonuçta ara
                hedef = _hedef_link_bul(driver, domain)
                etiket = domain
                # 2) bulunamazsa REKLAM (Ad/Sponsorlu) içinde ara
                if not hedef:
                    hedef = _reklam_link_bul(driver, domain)
                    if hedef:
                        etiket = f"[Ad] {domain}"
                if not hedef:
                    _log(log_cb, f"  ! '{domain}' ilk sayfada yok (organik+reklam), atlandı.")
                    continue
                _log(log_cb, f"  '{domain}' bulundu ({etiket}), tıklanıyor.")
                _siteyi_gez(driver, hedef, log_cb, etiket, serp_url)
                tiklanan += 1
                # SERP'e dönünce DOM yenilendi -> alt reklam/sonuçlar tekrar yüklensin
                if not iptal_mi():
                    _tum_sayfayi_kaydir(driver)
            _log(log_cb, f"  {tiklanan}/{len(domainler)} hedefe tıklandı.")
        else:
            # --- Hedef yok: ilk N sonuca tıkla ---
            for i in range(tiklama):
                if iptal_mi():
                    _log(log_cb, "İptal edildi.")
                    break
                sonuclar = driver.find_elements(By.CSS_SELECTOR, "a h3")
                sonuclar = [s for s in sonuclar if s.is_displayed()]
                if i >= len(sonuclar):
                    break
                _siteyi_gez(driver, sonuclar[i], log_cb, sonuclar[i].text[:60], serp_url)

        _log(log_cb, f"'{arama}' bitti.")

    finally:
        if not detach:
            try:
                driver.quit()
            except Exception:
                pass
            try:
                shutil.rmtree(profil, ignore_errors=True)
            except Exception:
                pass


# ---------------- ADB / Android uçak modu ----------------

ADB_VARSAYILAN = r"D:\Program Files\Microvirt\MEmu\adb.exe"


def _gomulu_adb():
    """exe içine gömülü ya da exe yanındaki adb.exe yolunu döndür (varsa)."""
    adaylar = []
    # PyInstaller onefile: gömülü dosyalar sys._MEIPASS'te
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        adaylar.append(os.path.join(mei, "adb.exe"))
    # exe / script yanı + platform-tools alt klasörü
    adaylar.append(os.path.join(MASAUSTU, "adb.exe"))
    adaylar.append(os.path.join(MASAUSTU, "platform-tools", "adb.exe"))
    for a in adaylar:
        if os.path.exists(a):
            return a
    return None


def adb_bul():
    """adb yolunu döndür: env > gömülü/exe-yanı > PATH > MEmu > 'adb'."""
    env = os.environ.get("ADB_PATH")
    if env and os.path.exists(env):
        return env
    g = _gomulu_adb()
    if g:
        return g
    p = shutil.which("adb")
    if p:
        return p
    if os.path.exists(ADB_VARSAYILAN):
        return ADB_VARSAYILAN
    return "adb"


def _adb(adb_yol, *args, seri=None, sn=15):
    """seri verilirse '-s <seri>' ile o cihaza yönlendir."""
    komut = [adb_yol]
    if seri:
        komut += ["-s", seri]
    komut += list(args)
    r = subprocess.run(komut, capture_output=True, text=True, timeout=sn)
    return (r.stdout + r.stderr).strip()


def adb_cihazlar(adb_yol=None):
    """
    Bağlı cihazları listele.
    Döner: [{'seri': str, 'durum': 'device'/'unauthorized'/..., 'model': str}, ...]
    """
    adb_yol = adb_yol or adb_bul()
    cikti = _adb(adb_yol, "devices", "-l")
    liste = []
    for satir in cikti.splitlines()[1:]:
        satir = satir.strip()
        if not satir:
            continue
        parcalar = satir.split()
        seri = parcalar[0]
        durum = parcalar[1] if len(parcalar) > 1 else "?"
        model = ""
        for p in parcalar[2:]:
            if p.startswith("model:"):
                model = p.split(":", 1)[1]
        liste.append({"seri": seri, "durum": durum, "model": model})
    return liste


def adb_cihaz_var(adb_yol, seri=None):
    """Yetkili (device) cihaz var mı? seri verilirse o cihaz yetkili mi?"""
    for c in adb_cihazlar(adb_yol):
        if c["durum"] != "device":
            continue
        if seri is None or c["seri"] == seri:
            return True
    return False


def ucak_modu(ac=True, adb_yol=None, seri=None, log_cb=None):
    """
    Bağlı Android telefonu uçak moduna al / çıkar.
    Android 11+ : 'cmd connectivity airplane-mode' (root gerekmez).
    Eski sürüm  : settings + broadcast (root gerekebilir).
      seri : hedef cihaz serisi (birden çok cihaz varsa gerekir).
    """
    adb_yol = adb_yol or adb_bul()
    durum = "enable" if ac else "disable"

    if not adb_cihaz_var(adb_yol, seri):
        _log(log_cb, "  ! Uçak modu: yetkili cihaz yok ya da seçili cihaz hazır değil.")
        return False

    # 1) Modern yöntem (Android 11+)
    cikti = _adb(adb_yol, "shell", "cmd", "connectivity", "airplane-mode", durum, seri=seri)
    if "Error" not in cikti and "Exception" not in cikti and "not found" not in cikti.lower():
        _log(log_cb, f"  ✓ Uçak modu {'AÇIK' if ac else 'KAPALI'} (connectivity).")
        return True

    # 2) Eski yöntem (settings + broadcast, root gerekebilir)
    _adb(adb_yol, "shell", "settings", "put", "global",
         "airplane_mode_on", "1" if ac else "0", seri=seri)
    yayin = _adb(adb_yol, "shell", "am", "broadcast", "-a",
                 "android.intent.action.AIRPLANE_MODE", "--ez", "state",
                 "true" if ac else "false", seri=seri)
    if "Broadcast completed" in yayin:
        _log(log_cb, f"  ✓ Uçak modu {'AÇIK' if ac else 'KAPALI'} (broadcast).")
        return True

    _log(log_cb, "  ! Uçak modu değiştirilemedi (Android sürümü eski / root gerekiyor).")
    return False


def ucak_modu_yenile(adb_yol=None, seri=None, bekle=15, geri_bekle=8, log_cb=None):
    """
    IP yenilemek için: uçak modu AÇ -> bekle sn -> KAPA -> geri_bekle sn.
      bekle      : uçak modunda kalma süresi (sn)
      geri_bekle : KAPA sonrası ağın geri gelmesi için bekleme (sn)
    """
    ok = ucak_modu(True, adb_yol, seri, log_cb)
    if ok:
        time.sleep(bekle)
        ucak_modu(False, adb_yol, seri, log_cb)
        _log(log_cb, f"  Ağ için {geri_bekle} sn bekleniyor...")
        time.sleep(geri_bekle)
    return ok


def main():
    arama = sys.argv[1] if len(sys.argv) > 1 else "python selenium tutorial"
    run_bot(arama)


if __name__ == "__main__":
    main()
