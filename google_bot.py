"""
Chrome aç -> Google ara -> sonuçlara tıkla -> mouse ile gez -> çık.
Hem komut satırından hem GUI panelinden (panel.py) çağrılabilir.

CLI:   python google_bot.py "arama kelimesi"
GUI:   run_bot(...) fonksiyonunu panel.py kullanır.
"""

import os
import re
import sys
import time
import random
import datetime
import shutil
import sqlite3
import tempfile
import subprocess

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    import undetected_chromedriver as uc
except Exception:
    uc = None

# Başlangıç sayfası. '/ncr' KULLANILMAZ (arayüzü İngilizce/ABD yapıyordu).
GOOGLE_URL = "https://www.google.com.tr/?hl=tr&gl=tr"


def _pathteki_chromedriver_gizle(log_cb=None):
    """PATH'te eski bir chromedriver.exe varsa Selenium onu kullanır ve
    'this version of chromedriver only supports chrome version XX' hatası verir.
    Çözüm: chromedriver.exe içeren klasörleri BU process'in PATH'inden çıkar ->
    Selenium Manager doğru sürümü kendisi indirir. (Sisteme dokunulmaz.)"""
    try:
        parcalar = os.environ.get("PATH", "").split(os.pathsep)
        temiz, atilan = [], []
        for p in parcalar:
            try:
                if p and os.path.isfile(os.path.join(p, "chromedriver.exe")):
                    atilan.append(p)
                    continue
            except Exception:
                pass
            temiz.append(p)
        if atilan:
            os.environ["PATH"] = os.pathsep.join(temiz)
            _log(log_cb, "PATH'te eski chromedriver bulundu, yok sayıldı: "
                         + "; ".join(atilan))
    except Exception:
        pass


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


# ---------------- Reklam domain veritabanı (yerel SQLite) ----------------
# Aramalarda görülen TÜM reklam (Ad/Sponsorlu) domainleri buraya kaydedilir.
# Dosya exe/script yanında: reklam_domainleri.db

# Bu domainler ASLA kaydedilmez (kendi sitem + Google gürültüsü).
# Kalıcı liste DB'deki 'engelli_domainler' tablosunda tutulur; aşağıdakiler
# tablo ilk oluşturulurken bir kez tohum olarak yazılır. Panelden yönetilir.
VARSAYILAN_ENGELLILER = {
    "tufantesisat.com.tr",
    "google.com", "google.com.tr", "gstatic.com",
    "googleadservices.com", "googlesyndication.com", "doubleclick.net",
}


def _engelli_kume():
    """Kara listeyi DB'den oku. DB açılamazsa varsayılanlara düş."""
    try:
        con = _db_baglan()
        satirlar = con.execute("SELECT domain FROM engelli_domainler").fetchall()
        con.close()
        return {r[0] for r in satirlar}
    except Exception:
        return set(VARSAYILAN_ENGELLILER)


def _engelli_mi(domain, kume=None):
    """domain kara listede mi? (alt alan adları da: x.google.com -> google.com)."""
    d = _temiz_domain(domain)
    if not d:
        return True
    if kume is None:
        kume = _engelli_kume()
    for e in kume:
        if d == e or d.endswith("." + e):
            return True
    return False


def engelli_listele():
    """Kara listedeki domainleri döndür: [(domain, eklenme), ...]."""
    try:
        con = _db_baglan()
        satirlar = con.execute(
            "SELECT domain, eklenme FROM engelli_domainler"
            " ORDER BY domain").fetchall()
        con.close()
        return satirlar
    except Exception:
        return []


def engelli_ekle(domain):
    """Kara listeye domain ekle; hedef DB'deki eşleşen kayıtları da temizler.

    Döner: eklendi ise True; geçersiz domain ya da zaten listede ise False.
    """
    d = _temiz_domain(domain)
    if not _gecerli_domain(d):
        return False
    try:
        con = _db_baglan()
        with con:
            cur = con.execute(
                "INSERT OR IGNORE INTO engelli_domainler(domain, eklenme)"
                " VALUES(?,?)", (d, time.strftime("%Y-%m-%d %H:%M:%S")))
            # bu domaine (ve alt alanlarına) ait hedef kayıtları artık kullanılmasın
            con.execute("DELETE FROM reklam_domainleri"
                        " WHERE domain=? OR domain LIKE ?", (d, "%." + d))
        con.close()
        return cur.rowcount > 0
    except Exception:
        return False


def engelli_sil(domain):
    """Kara listeden domain çıkar. Başarılıysa True."""
    d = _temiz_domain(domain)
    if not d:
        return False
    try:
        con = _db_baglan()
        with con:
            con.execute("DELETE FROM engelli_domainler WHERE domain=?", (d,))
        con.close()
        return True
    except Exception:
        return False


def _gecerli_domain(d):
    """Gerçek alan adı mı? tel:/mailto:/telefon no/yol içerenler ELENİR.

    Kural: en az bir nokta, sadece [a-z0-9.-], ':/@?# boşluk' yasak,
           TLD (son parça) harf ve >=2.
    """
    d = _temiz_domain(d)
    if not d or "." not in d:
        return False
    if any(c in d for c in (":", "/", " ", "@", "?", "#", "%")):
        return False
    izin = set("abcdefghijklmnopqrstuvwxyz0123456789.-")
    if any(c not in izin for c in d):
        return False
    tld = d.rsplit(".", 1)[-1]
    return len(tld) >= 2 and tld.isalpha()


def db_yolu():
    """Reklam domain DB'sinin tam yolu (exe/script klasörü)."""
    ozel = os.environ.get("REKLAM_DB_YOLU")
    if ozel:
        return ozel
    return os.path.join(MASAUSTU, "reklam_domainleri.db")


def _db_baglan():
    """DB'ye bağlan, tablo yoksa oluştur. Bağlantı döner."""
    con = sqlite3.connect(db_yolu(), timeout=10)
    # kara liste tablosu: ilk oluşturmada varsayılanlarla tohumla
    kara_var = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name='engelli_domainler'").fetchone()
    con.execute(
        "CREATE TABLE IF NOT EXISTS engelli_domainler("
        " domain TEXT PRIMARY KEY,"
        " eklenme TEXT)")
    if kara_var is None:
        su_an = time.strftime("%Y-%m-%d %H:%M:%S")
        con.executemany(
            "INSERT OR IGNORE INTO engelli_domainler(domain, eklenme)"
            " VALUES(?,?)", [(d, su_an) for d in sorted(VARSAYILAN_ENGELLILER)])
        con.commit()
    con.execute(
        "CREATE TABLE IF NOT EXISTS reklam_domainleri("
        " domain TEXT PRIMARY KEY,"
        " ilk_gorulme TEXT,"
        " son_gorulme TEXT,"
        " gorulme_sayisi INTEGER DEFAULT 0,"
        " son_arama TEXT,"
        " aramalar TEXT)")            # domainin bulunduğu TÜM aramalar (virgülle)
    # eski DB'de 'aramalar' kolonu yoksa ekle (migrasyon)
    try:
        kolonlar = [r[1] for r in con.execute("PRAGMA table_info(reklam_domainleri)")]
        if "aramalar" not in kolonlar:
            con.execute("ALTER TABLE reklam_domainleri ADD COLUMN aramalar TEXT")
        # boş 'aramalar' olan eski kayıtları son_arama ile doldur
        con.execute("UPDATE reklam_domainleri SET aramalar=son_arama"
                    " WHERE (aramalar IS NULL OR aramalar='') AND son_arama IS NOT NULL")
        con.commit()
    except Exception:
        pass
    # tıklama günlüğü: her başarılı giriş bir satır (istatistik sayfası okur)
    con.execute(
        "CREATE TABLE IF NOT EXISTS tiklamalar("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " zaman TEXT,"                  # 'YYYY-MM-DD HH:MM:SS' yerel saat
        " tarih TEXT,"                  # 'YYYY-MM-DD' (gün/hafta/ay gruplaması)
        " domain TEXT,"
        " arama TEXT,"
        " tur TEXT)")                   # 'reklam' | 'organik'
    con.execute("CREATE INDEX IF NOT EXISTS ix_tiklama_tarih"
                " ON tiklamalar(tarih)")
    con.commit()
    return con


def reklam_domain_kaydet(domainler, arama="", log_cb=None):
    """Reklam domainlerini DB'ye yaz (upsert): yeni ise ekle, varsa sayacı artır.

    domainler: domain string listesi (boş/None atlanır, tekilleştirilir).
    Döner: bu çağrıda İLK KEZ görülen (yeni eklenen) domain listesi.
    """
    temiz = sorted({_temiz_domain(d) for d in (domainler or []) if d and d.strip()})
    # sadece GERÇEK alan adları + kara liste dışı (tel:/mailto:/telefon no elenir)
    engelli = _engelli_kume()
    temiz = [d for d in temiz if _gecerli_domain(d) and not _engelli_mi(d, engelli)]
    if not temiz:
        return []
    su_an = time.strftime("%Y-%m-%d %H:%M:%S")
    yeni = []
    try:
        con = _db_baglan()
        with con:
            for d in temiz:
                onceki = con.execute(
                    "SELECT aramalar FROM reklam_domainleri WHERE domain=?",
                    (d,)).fetchone()
                if onceki is None:
                    # yeni domain
                    yeni.append(d)
                    aramalar = arama or ""
                    con.execute(
                        "INSERT INTO reklam_domainleri"
                        "(domain, ilk_gorulme, son_gorulme, gorulme_sayisi,"
                        " son_arama, aramalar) VALUES(?,?,?,1,?,?)",
                        (d, su_an, su_an, arama, aramalar))
                else:
                    # var olan: aramalar kümesini birleştir (domain hangi aramalarda çıktı)
                    mevcut = set(x for x in (onceki[0] or "").split(",") if x)
                    if arama:
                        mevcut.add(arama)
                    aramalar = ",".join(sorted(mevcut))
                    con.execute(
                        "UPDATE reklam_domainleri SET son_gorulme=?,"
                        " gorulme_sayisi=gorulme_sayisi+1, son_arama=?, aramalar=?"
                        " WHERE domain=?",
                        (su_an, arama, aramalar, d))
        con.close()
        if yeni:
            _log(log_cb, f"  DB: {len(yeni)} yeni reklam domaini kaydedildi "
                         f"({', '.join(yeni)}).")
    except Exception as ex:
        _log(log_cb, f"  DB yazma hatası: {str(ex)[:70]}")
    return yeni


def reklam_domainleri_listele(sirala="son_gorulme"):
    """Kayıtlı tüm reklam domainlerini döndür: [(domain, sayi, ilk, son, arama), ...]."""
    izin = {"son_gorulme", "ilk_gorulme", "gorulme_sayisi", "domain"}
    kol = sirala if sirala in izin else "son_gorulme"
    try:
        con = _db_baglan()
        satirlar = con.execute(
            f"SELECT domain, gorulme_sayisi, ilk_gorulme, son_gorulme, son_arama"
            f" FROM reklam_domainleri ORDER BY {kol} DESC").fetchall()
        con.close()
        return satirlar
    except Exception:
        return []


def hedef_domainler_db(arama=None):
    """Hedef domainleri döndür.

    arama verilirse: SADECE o aramada bulunmuş domainler + elle eklenenler ('manuel').
      (Bir tesisat reklamı sadece kendi kelimesinde çıkar; alakasız aramada boşuna
       denenmesin -> 'reklamda yok, atlandı' gürültüsü olmasın.)
    arama None ise: tüm domainler.
    """
    try:
        con = _db_baglan()
        satirlar = con.execute(
            "SELECT domain, aramalar FROM reklam_domainleri").fetchall()
        con.close()
    except Exception:
        return []
    if arama is None:
        return [r[0] for r in satirlar if r and r[0]]
    a = (arama or "").strip().lower()
    out = []
    for dom, aramalar in satirlar:
        if not dom:
            continue
        kume = set(x.strip().lower() for x in (aramalar or "").split(",") if x.strip())
        if a in kume or "manuel" in kume:
            out.append(dom)
    return out


def reklam_domain_sil(domain):
    """Bir domaini DB'den sil. Başarılıysa True."""
    d = _temiz_domain(domain)
    if not d:
        return False
    try:
        con = _db_baglan()
        with con:
            con.execute("DELETE FROM reklam_domainleri WHERE domain=?", (d,))
        con.close()
        return True
    except Exception:
        return False


# ---------------- Tıklama günlüğü / istatistik ----------------

AY_KISA = ["Oca", "Şub", "Mar", "Nis", "May", "Haz",
           "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]

# periyot -> (SQLite strftime kalıbı, kaç kova gösterilir)
PERIYOTLAR = {"gun": ("%Y-%m-%d", 30), "hafta": ("%Y-%W", 12), "ay": ("%Y-%m", 12)}


def tiklama_kaydet(domain, arama="", tur="reklam", log_cb=None):
    """Başarılı bir tıklamayı (siteye giriş) günlüğe yaz. Başarılıysa True."""
    d = _temiz_domain(domain or "")
    if not d:
        return False
    su_an = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        con = _db_baglan()
        with con:
            con.execute(
                "INSERT INTO tiklamalar(zaman, tarih, domain, arama, tur)"
                " VALUES(?,?,?,?,?)",
                (su_an, su_an[:10], d, arama or "", tur))
        con.close()
        return True
    except Exception as ex:
        _log(log_cb, f"  Tıklama kaydı hatası: {str(ex)[:70]}")
        return False


def _tur_filtre(tur):
    """(sql parçası, parametreler) — tur None ise filtre yok."""
    if tur in ("reklam", "organik"):
        return " AND tur=?", [tur]
    return "", []


def tiklama_toplam(gun=1, tur=None):
    """Son `gun` gündeki (bugün dahil) toplam tıklama sayısı."""
    basla = (datetime.date.today() - datetime.timedelta(days=max(1, gun) - 1)
             ).strftime("%Y-%m-%d")
    ek, par = _tur_filtre(tur)
    try:
        con = _db_baglan()
        n = con.execute(
            "SELECT COUNT(*) FROM tiklamalar WHERE tarih>=?" + ek,
            [basla] + par).fetchone()[0]
        con.close()
        return int(n or 0)
    except Exception:
        return 0


def _kova_listesi(periyot, adet):
    """Son `adet` kovanın (anahtar, etiket, başlangıç tarihi) listesi — eskiden yeniye."""
    bugun = datetime.date.today()
    out = []
    if periyot == "ay":
        y, a = bugun.year, bugun.month
        for _ in range(adet):
            out.append((f"{y:04d}-{a:02d}", f"{AY_KISA[a - 1]} {y}",
                        datetime.date(y, a, 1)))
            a -= 1
            if a == 0:
                y, a = y - 1, 12
    elif periyot == "hafta":
        # haftanın pazartesisi (SQLite %W ile aynı: pazartesi başlangıçlı)
        bas = bugun - datetime.timedelta(days=bugun.weekday())
        for i in range(adet):
            g = bas - datetime.timedelta(weeks=i)
            out.append((g.strftime("%Y-%W"),
                        f"{g.day} {AY_KISA[g.month - 1]}", g))
    else:
        for i in range(adet):
            g = bugun - datetime.timedelta(days=i)
            out.append((g.strftime("%Y-%m-%d"),
                        f"{g.day} {AY_KISA[g.month - 1]}", g))
    return list(reversed(out))


def tiklama_seri(periyot="gun", tur=None):
    """Grafik verisi: [(anahtar, etiket, adet), ...] eskiden yeniye, boş kovalar 0."""
    kalip, adet = PERIYOTLAR.get(periyot, PERIYOTLAR["gun"])
    kovalar = _kova_listesi(periyot, adet)
    basla = kovalar[0][2].strftime("%Y-%m-%d")
    ek, par = _tur_filtre(tur)
    sayilar = {}
    try:
        con = _db_baglan()
        for k, n in con.execute(
                f"SELECT strftime('{kalip}', tarih) k, COUNT(*)"
                f" FROM tiklamalar WHERE tarih>=?{ek} GROUP BY k",
                [basla] + par):
            sayilar[k] = n
        con.close()
    except Exception:
        pass
    return [(k, etiket, int(sayilar.get(k, 0))) for k, etiket, _ in kovalar]


def tiklama_kirilim(periyot="gun", kova=None, tur=None):
    """Bir kovadaki site kırılımı: [(domain, adet, reklam_adet, organik_adet), ...]."""
    kalip, _ = PERIYOTLAR.get(periyot, PERIYOTLAR["gun"])
    if not kova:
        return []
    ek, par = _tur_filtre(tur)
    try:
        con = _db_baglan()
        satirlar = con.execute(
            f"SELECT domain, COUNT(*) n,"
            f" SUM(CASE WHEN tur='reklam' THEN 1 ELSE 0 END),"
            f" SUM(CASE WHEN tur!='reklam' THEN 1 ELSE 0 END)"
            f" FROM tiklamalar WHERE strftime('{kalip}', tarih)=?{ek}"
            f" GROUP BY domain ORDER BY n DESC, domain", [kova] + par).fetchall()
        con.close()
        return [(d, int(n or 0), int(r or 0), int(o or 0)) for d, n, r, o in satirlar]
    except Exception:
        return []


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
  //    Değerler çalışma başına RASTGELE (aşağıdaki yer-tutucular Python'da doldurulur)
  //    -> her oturum farklı donanım parmak izi, kümelenip yakalanmaz.
  try {
    const yama = (proto) => {
      const orj = proto.getParameter;
      proto.getParameter = function (p) {
        if (p === 37445) return '__WEBGL_VENDOR__';    // UNMASKED_VENDOR
        if (p === 37446) return '__WEBGL_RENDERER__';  // UNMASKED_RENDERER
        return orj.call(this, p);
      };
    };
    if (window.WebGLRenderingContext) yama(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) yama(WebGL2RenderingContext.prototype);
  } catch (e) {}

  // 7) hardwareConcurrency / deviceMemory — çalışma başına rastgele (gerçekçi)
  try { Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => __CORES__}); } catch (e) {}
  try { Object.defineProperty(navigator, 'deviceMemory', {get: () => __MEM__}); } catch (e) {}

  // 8) Notification.permission 'default' (otomasyonda bazen 'denied')
  try {
    if (window.Notification && Notification.permission === 'denied') {
      Object.defineProperty(Notification, 'permission', {get: () => 'default'});
    }
  } catch (e) {}

  // 9) User-Agent Client Hints — mobil emülasyonda platform 'Windows' sızıyordu.
  //    (Python yer-tutucusu: masaüstünde boş, mobilde Android UA-CH ile doldurulur.)
  __UACH_BLOCK__
})();
"""


# GPU vendor/renderer havuzu — gerçek Windows makinelerde yaygın kartlar.
# Her (vendor, renderer) çifti tutarlı; çalışma başına rastgele biri seçilir.
_GPU_HAVUZ = [
    ('Google Inc. (Intel)',
     'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    ('Google Inc. (Intel)',
     'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    ('Google Inc. (Intel)',
     'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    ('Google Inc. (NVIDIA)',
     'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    ('Google Inc. (NVIDIA)',
     'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    ('Google Inc. (NVIDIA)',
     'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    ('Google Inc. (AMD)',
     'ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)'),
    ('Google Inc. (AMD)',
     'ANGLE (AMD, Radeon RX 580 Series Direct3D11 vs_5_0 ps_5_0, D3D11)'),
]


def _parmak_izi_uret(mobil=False):
    """Çalışma başına tutarlı donanım parmak izi seç.

    mobil=True: UA Pixel 7 (Tensor G2 / Mali GPU) olduğu için WebGL de MOBİL
    olmalı — masaüstü Intel/NVIDIA dizesi verirsek UA ile çelişir, bot sinyali.
    """
    if mobil:
        # Pixel 7 ile tutarlı sabit mobil parmak izi (UA sabit olduğu için bu da sabit).
        return {
            "vendor": "Google Inc. (ARM)",
            "renderer": "ANGLE (ARM, Mali-G710 MC10, OpenGL ES 3.2)",
            "cores": 8,
            "mem": 8,
        }
    vendor, renderer = random.choice(_GPU_HAVUZ)
    # deviceMemory Chrome'da 8 ile tavanlanır; cores gerçekçi değerler.
    return {
        "vendor": vendor,
        "renderer": renderer,
        "cores": random.choice([4, 6, 8, 8, 12, 16]),
        "mem": random.choice([4, 8, 8]),
    }


def _uach_blok_mobil(major):
    """Mobil emülasyonda navigator.userAgentData'yı Android/Pixel 7 ile tutarlı yap.

    Emülasyonda UA dizesi 'Android' der ama Client Hints platform 'Windows'
    sızdırır -> çelişki. Burada userAgentData'yı Android olarak yeniden tanımlarız.
    """
    return r"""
  try {
    const M = '__MAJOR__';
    const brands = [
      {brand: 'Chromium', version: M},
      {brand: 'Google Chrome', version: M},
      {brand: 'Not-A.Brand', version: '99'}
    ];
    const fullVer = M + '.0.0.0';
    const uaData = {
      brands: brands,
      mobile: true,
      platform: 'Android',
      getHighEntropyValues: function (hints) {
        return Promise.resolve({
          brands: brands,
          mobile: true,
          platform: 'Android',
          platformVersion: '13.0.0',
          architecture: '',
          bitness: '',
          model: 'Pixel 7',
          uaFullVersion: fullVer,
          fullVersionList: [
            {brand: 'Chromium', version: fullVer},
            {brand: 'Google Chrome', version: fullVer},
            {brand: 'Not-A.Brand', version: '99.0.0.0'}
          ]
        });
      },
      toJSON: function () { return {brands: brands, mobile: true, platform: 'Android'}; }
    };
    Object.defineProperty(navigator, 'userAgentData', {get: () => uaData});
  } catch (e) {}
""".replace("__MAJOR__", str(major))


def _stealth_js_uret(fp=None, mobil=False):
    """STEALTH_JS şablonundaki yer-tutucuları parmak izi + UA-CH ile doldur."""
    fp = fp or _parmak_izi_uret(mobil=mobil)
    uach = _uach_blok_mobil(_chrome_major() or 124) if mobil else ""
    return (STEALTH_JS
            .replace("__WEBGL_VENDOR__", fp["vendor"])
            .replace("__WEBGL_RENDERER__", fp["renderer"])
            .replace("__CORES__", str(fp["cores"]))
            .replace("__MEM__", str(fp["mem"]))
            .replace("__UACH_BLOCK__", uach))


def _stealth_uygula(driver, fp=None, mobil=False):
    """Rastgele parmak izli stealth JS'i yeni dokümanlar için kaydet + mevcut sayfaya enjekte et."""
    kaynak = _stealth_js_uret(fp or _parmak_izi_uret(mobil=mobil), mobil=mobil)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": kaynak}
        )
    except Exception:
        pass
    try:
        driver.execute_script(kaynak)
    except Exception:
        pass


def _log(cb, mesaj):
    """cb varsa GUI'ye gönder, yoksa konsola yaz."""
    if cb:
        cb(mesaj)
    else:
        print(mesaj)


def insanca_bekle(a=0.6, b=1.8, maks=None):
    """İnsan gibi DÜZENSİZ bekleme. Tek tip uniform değil; ağırlıklı karışık dağılım.

    Patternler:
      ~%15 hızlı tepki   : [a*0.4, a*0.9]
      ~%62 normal        : gauss(merkez, yayılım)
      ~%15 okuma/düşünme : [b, b*2.2]
      ~%5  dalgınlık     : [b*2.5, b*4.5]
    Üstüne mikro jitter (gauss) eklenir.
    maks: üst sınır (sn) — site içi gezinmede uzun 'dalgınlık' donması olmasın diye.
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
    if maks is not None:
        t = min(t, maks)
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
    """Sayfada OKUR gibi gez: yukarıdan aşağı ilerle, ara sıra viewport içindeki
    öğeye hover / küçük yukarı düzeltme yap; dibe varınca durur.

    dongu: gezinme bütçesi (eski çağrılarla uyumlu) — adım sayısı sayfa
    boyundan türetilir, dongu ile tavanlanır. Rastgele elemana scrollIntoView
    YOK: ilk 30 eleman hep sayfa tepesiydi, gezinme yoyo gibi zıplıyordu.
    """
    try:
        eb = driver.execute_script(
            "return [document.body.scrollHeight, innerHeight, innerWidth];")
        toplam, gh, gw = eb[0], eb[1], eb[2]
    except Exception:
        return

    # okunacak derinlik: sayfanın %70-100'ü; adım bütçesi boy + dongu'dan
    hedef_derinlik = toplam * random.uniform(0.7, 1.0)
    max_adim = max(2, min(dongu * 3, int(hedef_derinlik / 400) + 1))

    for _ in range(max_adim):
        # ara sıra KISA fare süzülmesi (uzun bezier turu yok — görünmez ve
        # isTrusted=false olduğundan tespit değeri sıfır, süresi kısaltıldı)
        if random.random() < 0.4:
            _fare_kaydir(driver, random.randint(40, gw - 40),
                         random.randint(40, gh - 40), adim=random.randint(3, 6))

        # ara sıra ŞU AN viewport içinde duran bir öğeye hover (scroll tetiklemez)
        if random.random() < 0.35:
            try:
                hedef = driver.execute_script(
                    """
                    var els = document.querySelectorAll('a, h2, h3, p, img, button');
                    var ic = [];
                    for (var i = 0; i < els.length && ic.length < 20; i++) {
                        var r = els[i].getBoundingClientRect();
                        if (r.width > 20 && r.height > 10 &&
                            r.top > 40 && r.bottom < innerHeight - 10)
                            ic.push(els[i]);
                    }
                    return ic.length ? ic[Math.floor(arguments[0] * ic.length)] : null;
                    """,
                    random.random(),
                )
                if hedef:
                    ActionChains(driver).move_to_element(hedef).perform()
                    time.sleep(random.uniform(0.2, 0.6))
            except Exception:
                pass

        # asıl ilerleme: aşağı kaydır (gerçek telefonda parmakla)
        if _dokunma_var(driver):
            _adb_kaydir_asagi(getattr(driver, "_adb_yol", None),
                              getattr(driver, "_adb_seri", None),
                              getattr(driver, "_ekran", None))
        else:
            if random.random() < 0.15:   # okurken küçük geri dönüş
                _gercek_scroll(driver, -random.randint(80, 220))
                time.sleep(random.uniform(0.3, 0.7))
            _gercek_scroll(driver, random.randint(250, 650))
        insanca_bekle(0.5, 1.2, maks=3.0)

        # dibe vardıysa kısa bak, bitir — boş scrollBy'la bekleme yapma
        try:
            kon = driver.execute_script(
                "return [Math.round(scrollY + innerHeight), document.body.scrollHeight];")
            if kon[0] >= kon[1] - 40:
                insanca_bekle(0.4, 1.0, maks=2.0)
                break
        except Exception:
            break


def _dokunma_var(driver):
    """Gerçek telefonda ADB dokunma enjeksiyonu kullanılabilir mi?
    MIUI/HyperOS 'USB hata ayıklama (Güvenlik ayarları)' kapalıysa input tap/swipe
    SecurityException (INJECT_EVENTS) ile reddedilir -> Selenium/JS'e düşülür."""
    return (getattr(driver, "_gercek", False)
            and not getattr(driver, "_dokunma_yok", False))


def _gercek_scroll(driver, dy):
    """Gerçek fare-tekerlek olayı üret (CDP Input -> isTrusted=true).

    JS window.scrollBy programatik sayılır; Google SERP kendi sayfasında scroll
    davranışını izler. ActionChains.scroll_by_amount CDP üzerinden GERÇEK wheel
    olayı gönderir. Desteklenmezse JS scrollBy'a düşer.
    """
    try:
        ActionChains(driver).scroll_by_amount(0, int(dy)).perform()
        return True
    except Exception:
        try:
            driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
        except Exception:
            pass
        return False


def _tum_sayfayi_kaydir(driver, max_adim=30):
    """Sayfayı parça parça EN ALTA kadar indir (lazy sonuç + alt reklamlar yüklensin), sonra başa dön.

    Aşağıdaki organik sonuçlar ve #bottomads reklamları ancak scroll'la DOM'a gelir.
    NOT: 'behavior:smooth' KULLANILMAZ — animasyon bitmeden scrollY okununca
    erken 'dibe vardı' sanılıp duruyordu. Gerçek wheel + bekleme ile ölç.
    """
    try:
        son_y = -1
        durgun = 0
        gercek = _dokunma_var(driver)
        for _ in range(max_adim):
            if gercek:
                # gerçek telefon: parmakla kaydır (dokunma olayları + inertia)
                _adb_kaydir_asagi(getattr(driver, "_adb_yol", None),
                                  getattr(driver, "_adb_seri", None),
                                  getattr(driver, "_ekran", None))
            else:
                _gercek_scroll(driver, random.randint(350, 650))
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
    # Oturum açık profilde çerez penceresi genelde çıkmaz -> kısa timeout.
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


# Reklam ağı ara host'ları: gerçek hedef DEĞİL, sadece yönlendirici.
AG_HOSTLARI = ("googleadservices.com", "googlesyndication.com",
               "doubleclick.net", "googleusercontent.com", "gstatic.com",
               "googletagmanager.com")


def _ag_hosti(host):
    """Host bir Google/reklam ağı host'u mu (yani hedef site değil)?"""
    h = _temiz_domain(host or "")
    if not h:
        return True
    if h == "google.com" or h.startswith("google.") or ".google." in h:
        return True
    return any(h == a or h.endswith("." + a) for a in AG_HOSTLARI)


def _gomulu_hostlar(metin):
    """Metindeki (href/attribute) gömülü http(s) adreslerin host'ları, sırayla.

    aclk href'inde hedef URL yüzde-kodlu gömülü olabilir (adurl=, &url=, ai=...).
    Tekrar tekrar unquote edip TÜM http(s) adreslerini çıkarır.
    """
    import urllib.parse as up
    s = metin or ""
    for _ in range(3):
        yeni = up.unquote(s)
        if yeni == s:
            break
        s = yeni
    return [_temiz_domain(m) for m in
            re.findall(r'https?://([^/\s"\'\\,\]\)}<>]+)', s)]


def _yazidan_host(metin):
    """Reklamın görünen adresinden host çıkar ('https://www.site.com › yol')."""
    ilk = ((metin or "").strip().splitlines() or [""])[0]
    t = _temiz_domain(ilk)
    t = re.split(r"[\s›|/?#,]", t)[0].strip(".")
    return t if ("." in t and " " not in t) else ""


def _domain_es(a, b):
    """İki domain eşleşiyor mu (alt alan adı iki yönlü)."""
    a, b = _temiz_domain(a or ""), _temiz_domain(b or "")
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _reklam_hedef_domain(bilgi):
    """Reklam linkinin GERÇEK hedef domaini.

    Sıra: href içindeki ilk ağ-dışı host -> data-pcu/data-rw -> görünen adres
    (cite / data-dtld). Google 'aclk' bağlantısında hedef bazen href'te HİÇ
    geçmez (sunucu tarafı yönlendirme) -> o zaman cite/pcu kurtarır.
    Eskiden bu durumda domain 'google.com' çıkıyor ve site listede olsa bile
    'reklamda yok, atlandı' deniyordu.
    """
    for alan in ("href", "pcu"):
        for h in _gomulu_hostlar(bilgi.get(alan) or ""):
            if not _ag_hosti(h):
                return h
    for alan in ("dtld", "cite"):
        h = _yazidan_host(bilgi.get(alan) or "")
        if h and not _ag_hosti(h):
            return h
    return ""


def _reklam_bilgileri(driver):
    """Sayfadaki TÜM reklam linkleri + hedef ipuçları.

    Döner: [{"el":WebElement, "href":str, "pcu":str, "cite":str, "dtld":str,
             "domain":str}]  (domain = çözülmüş gerçek hedef, boş olabilir)

    Tespit JS ile yapılır -> Google'ın sık değişen reklam DOM'una dayanıklı:
      - reklam konteyneri içinde mi (#tads/#tadsb/#bottomads/#taw, data-text-ad/pcu/rw, aria-label)
      - 'Sponsorlu/Sponsored/Reklam' etiketi yakınında mı
      - href reklam izi taşıyor mu (aclk/adservices/pagead/adurl)
    Ayrıca her link için hedef ipuçları toplanır (data-pcu/data-rw/data-dtld/cite)
    çünkü aclk href'i hedefi taşımayabilir.
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
    // hedef ipuçları: aclk href'i hedefi taşımayabilir
    const ipucu = (a) => {
      let pcu = '', cite = '', dtld = '';
      let p = a;
      for (let i = 0; i < 8 && p; i++, p = p.parentElement) {
        if (!p.getAttribute) continue;
        if (!pcu)  pcu  = p.getAttribute('data-pcu') || p.getAttribute('data-rw') || '';
        if (!dtld) dtld = p.getAttribute('data-dtld') || p.getAttribute('data-hveid-url') || '';
        if (!cite && p.querySelector) {
          const c = p.querySelector('cite, [role="text"] cite, span[class*="cite"]');
          if (c) cite = (c.innerText || '').trim();
        }
        if (pcu && cite) break;
      }
      return {pcu: pcu, cite: cite, dtld: dtld};
    };
    document.querySelectorAll('a[href]').forEach(a => {
      const href = a.href || '';
      if (!href || seen.has(href)) return;
      if (hrefAd(href) || reklamKonteyner(a) || sponEtiket(a)) {
        const r = a.getBoundingClientRect();
        if (a.offsetParent !== null || r.width > 0 || r.height > 0) {
          seen.add(href);
          const ip = ipucu(a);
          out.push({el: a, href: href, pcu: ip.pcu, cite: ip.cite, dtld: ip.dtld});
        }
      }
    });
    return out;
    """
    try:
        ham = driver.execute_script(js) or []
    except Exception:
        return []
    bilgiler = []
    for b in ham:
        try:
            b = dict(b)
            b["domain"] = _reklam_hedef_domain(b)
            bilgiler.append(b)
        except Exception:
            continue
    return bilgiler


def _reklam_linkleri(driver):
    """Sayfadaki TÜM reklam link ÖĞELERİ (görünür, tekrarsız)."""
    return [b["el"] for b in _reklam_bilgileri(driver) if b.get("el") is not None]


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
    """Reklamlar arasında hedefi domain ile eşleşen ilk görünür linki döndür.

    Eşleşme çözülmüş hedef domain üzerinden (href + data-pcu + görünen adres),
    sadece href host'u değil -> aclk href'i hedefi taşımasa da bulunur.
    """
    domain = _temiz_domain(domain)
    if not domain:
        return None
    for b in _reklam_bilgileri(driver):
        try:
            if _domain_es(b.get("domain") or "", domain):
                return b.get("el")
            if _host_es(b.get("href") or "", domain):
                return b.get("el")
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
        insanca_bekle(1.5, 3.0, maks=4.5)
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
    insanca_bekle()

    tiklandi = False
    if _dokunma_var(driver):
        # gerçek telefon: parmakla dokun (ADB input tap). Işlemezse normal tıklamaya düş.
        onceki_url = driver.current_url
        tiklandi = _gercek_tikla(driver, hedef, onceki_handles, onceki_url)
        if not tiklandi:
            if getattr(driver, "_dokunma_yok", False):
                _log(log_cb, "  (adb dokunma engelli/MIUI -> Selenium tıklama kullanılıyor)")
            else:
                _log(log_cb, "  (parmak dokunuşu işlemedi, normal tıklamaya geçildi)")
    if not tiklandi:
        try:
            ActionChains(driver).move_to_element(hedef).perform()
        except Exception:
            pass
        try:
            hedef.click()
        except Exception:
            driver.execute_script("arguments[0].click();", hedef)

    insanca_bekle(1.5, 3.0, maks=4.5)

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


def _yeni_sekmede_ac(driver, el, log_cb, etiket, serp_url=None, gez_dongu=5):
    """Reklama CTRL+tık -> GERÇEK tıklama jesti ama YENİ SEKMEDE açılır.

    Böylece SERP sekmesi olduğu gibi kalır: arama sayfası tekrar tekrar
    yüklenmez, reklam listesi/DOM bozulmaz, sıradaki reklama direkt geçilir.
    gclid + referer korunur (driver.get(aclk) değil, gerçek tık).

    Döner: True  = girildi (yeni sekme ya da aynı sekmede gidip SERP'e dönüldü)
           False = tık işlemedi -> çağıran eski yola düşsün.
    Gerçek telefonda (dokunma) ctrl yok -> hiç denenmez, False döner.
    """
    if _dokunma_var(driver):
        return False
    ana = driver.current_window_handle
    onceki = set(driver.window_handles)
    onceki_url = driver.current_url
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        insanca_bekle(0.4, 1.0, maks=1.6)
        (ActionChains(driver)
         .move_to_element(el)
         .key_down(Keys.CONTROL)
         .click(el)
         .key_up(Keys.CONTROL)
         .perform())
    except Exception:
        try:
            ActionChains(driver).key_up(Keys.CONTROL).perform()
        except Exception:
            pass
        return False

    yeni = set()
    for _ in range(20):                      # yeni sekme ~5 sn içinde açılır
        try:
            yeni = set(driver.window_handles) - onceki
        except Exception:
            yeni = set()
        if yeni:
            break
        time.sleep(0.25)

    if not yeni:
        # ctrl işlemedi ve sayfa aynı sekmede gittiyse: gez, SERP'e dön
        try:
            gitti = driver.current_url != onceki_url
        except Exception:
            gitti = False
        if not gitti:
            return False
        _log(log_cb, f"  -> (aynı sekmede) {etiket}")
        mouse_gezin(driver, dongu=gez_dongu)
        try:
            driver.get(serp_url) if serp_url else driver.back()
            sonuc_bekle(driver, 20)
        except Exception:
            pass
        return True

    _log(log_cb, f"  -> yeni sekmede: {etiket}")
    try:
        driver.switch_to.window(yeni.pop())
        insanca_bekle(1.5, 3.0, maks=4.5)
        mouse_gezin(driver, dongu=gez_dongu)   # sitede gez
    except Exception as ex:
        _log(log_cb, f"  ! '{etiket}' ziyaret hatası: {str(ex)[:70]}")
    finally:
        try:
            if driver.current_window_handle != ana:
                driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(ana)
        except Exception:
            if driver.window_handles:
                driver.switch_to.window(driver.window_handles[0])
    return True


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


def _reklam_domainleri_topla(driver, log_cb, arama=""):
    """SERP'teki TÜM reklam domainlerini çıkar, LOGLA ve DB'ye KAYDET."""
    bulunan = []
    try:
        for b in _reklam_bilgileri(driver):
            rd = b.get("domain") or ""
            if rd and rd not in bulunan:
                bulunan.append(rd)
    except Exception:
        pass
    if bulunan:
        _log(log_cb, f"  Reklam domainleri ({len(bulunan)}): {', '.join(bulunan)}")
        reklam_domain_kaydet(bulunan, arama, log_cb)   # yerel DB'ye yaz
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


def _reklam_haritasi(driver, log_cb=None, arama="", etiket="Tarama"):
    """Sayfayı en alta kadar süz, TÜM reklamları (domain, href) olarak döndür.

    Döner: [(domain, href)] DOM sırasıyla (üstten alta), domain başına ilk link.
    Bulunan domainler DB'ye de yazılır (girilsin girilmesin).
    """
    konum_popup_kapat(driver)
    _tum_sayfayi_kaydir(driver)
    reklamlar = []
    gorulen = set()
    cozulemeyen = 0
    ornek = ""
    for b in _reklam_bilgileri(driver):
        href = b.get("href") or ""
        dom = b.get("domain") or ""
        if not dom:
            cozulemeyen += 1
            ornek = ornek or href[:90]
            continue
        if href and dom not in gorulen:
            gorulen.add(dom)
            reklamlar.append((dom, href))
    reklam_domain_kaydet([d for d, _ in reklamlar], arama, log_cb)
    _log(log_cb, f"  {etiket}: {len(reklamlar)} reklam bulundu "
                 f"({', '.join(d for d, _ in reklamlar) or '-'}).")
    if cozulemeyen:
        _log(log_cb, f"  ! {cozulemeyen} reklam linkinin hedefi çözülemedi "
                     f"(örn: {ornek})")
    return reklamlar


def _reklamlari_sirayla_isle(driver, domainler, log_cb, serp_url, iptal_mi,
                             gez_dongu=5, arama=""):
    """SADECE ilk sonuç sayfası: BİR KEZ tara, listeyle eşleşen reklamlara
    sayfadaki sırayla TEK TEK gir.

    Reklamlar CTRL+tık ile YENİ SEKMEDE açılır -> arama sayfası hiç yeniden
    yüklenmez (eskiden her reklamdan sonra SERP tekrar açılıyor + tekrar
    taranıyordu; ekranda arka arkaya bir sürü arama sayfası geziyordu).
    Tıklama yine GERÇEK tık jesti: gclid + referer korunur.

    Sadece aynı sekmede açılmak zorunda kalınırsa (gerçek telefon / ctrl
    işlemezse) SERP bir kez geri yüklenir; sıradaki reklam taze DOM'da aranır.
    """
    # liste sırasını koru, tekrarsız
    liste = []
    for d in domainler:
        td = _temiz_domain(d)
        if td and td not in liste:
            liste.append(td)

    # --- TEK tarama: ilk sayfadaki tüm reklamlar (DOM sırasıyla) ---
    reklamlar = _reklam_haritasi(driver, log_cb, arama, "Sayfa taraması")

    # sayfadaki sırayla, listede olan reklamlar
    sirali = []
    for dom, href in reklamlar:
        hedef_dom = next((t for t in liste if _domain_es(dom, t)), None)
        if hedef_dom and not any(d == hedef_dom for d, _ in sirali):
            sirali.append((hedef_dom, href))

    yok = len(liste) - len(sirali)
    _log(log_cb, f"  Listeyle eşleşen reklam: {len(sirali)} "
                 f"({', '.join(d for d, _ in sirali) or '-'})"
                 + (f"; {yok} site bu sayfada reklamda yok." if yok > 0 else ""))

    girilen = 0
    for domain, href in sirali:
        if iptal_mi():
            _log(log_cb, "İptal edildi.")
            break
        # öğeyi taze bul (aynı sekmede gidilip dönüldüyse DOM yenilenmiş olur)
        hedef = _reklam_link_bul(driver, domain)
        if not hedef:
            _tum_sayfayi_kaydir(driver)             # alt reklamlar DOM'a gelsin
            hedef = _reklam_link_bul(driver, domain)
        if hedef is not None:
            _log(log_cb, f"  ✓ '{domain}' -> tıklanıyor (gerçek tık)")
            if not _yeni_sekmede_ac(driver, hedef, log_cb, f"[Ad] {domain}",
                                    serp_url, gez_dongu):
                # ctrl+tık işlemedi -> aynı sekme yolu (dönüşte SERP yenilenir)
                _siteyi_gez(driver, hedef, log_cb, f"[Ad] {domain}",
                            serp_url, gez_dongu)
        else:
            # öğe kayboldu (SERP yenilendi vb.) -> son çare href'i aç
            _log(log_cb, f"  ✓ '{domain}' -> giriliyor (öğe yok, doğrudan)")
            _ziyaret_href(driver, href, f"[Ad] {domain}", log_cb, gez_dongu)
        girilen += 1
        tiklama_kaydet(domain, arama, "reklam", log_cb)

    _log(log_cb, f"  Sayfa bitti: {girilen}/{len(sirali)} reklama girildi.")
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

    # PATH'teki eski chromedriver'ı yok say -> Selenium Manager doğrusunu indirsin
    _pathteki_chromedriver_gizle(log_cb)

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
        _adb_yol = adb_bul()
        try:
            adb_dir = os.path.dirname(_adb_yol)
            if adb_dir and adb_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = adb_dir + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass
        # Chrome açılmadan önce telefonu hazırla: uyandır, kilit aç, mobil veri (operatör IP)
        telefon_hazirla(_adb_yol, cihaz_seri, mobil_veri=True, log_cb=log_cb)
        op = webdriver.ChromeOptions()
        op.add_experimental_option("androidPackage", "com.android.chrome")
        # Oturum/çerez KORUNMAZ: Chrome'u chromedriver başlatır ve açılışta
        # veriyi temizler (temiz profil). Kalıcı profilde Google kişiselleştirmesi
        # yüzünden aramalarda reklam çıkmıyordu -> androidUseRunningApp kullanılmaz.
        if cihaz_seri:
            op.add_experimental_option("androidDeviceSerial", cihaz_seri)
        op.add_argument("--disable-blink-features=AutomationControlled")
        op.add_experimental_option("excludeSwitches", ["enable-automation"])
        op.add_argument("--lang=tr-TR")
        # Telefondaki Chrome sürümünü oku -> AYNI major'da chromedriver indir.
        # (Selenium Manager PC'deki Chrome'a göre seçer -> telefonla uyuşmaz!)
        tel_major = telefon_chrome_major(_adb_yol, cihaz_seri)
        surucu = _chromedriver_indir(tel_major, log_cb) if tel_major else None
        _log(log_cb, f"Telefon Chrome'u açılıyor (ADB)... "
                     f"cihaz: {cihaz_seri or 'otomatik'}, "
                     f"telefon Chrome: {tel_major or '?'}, arama: '{arama}'")
        if not surucu:
            _log(log_cb, "Uyarı: telefon Chrome sürümü/driver alınamadı, "
                         "Selenium Manager deneniyor (sürüm uyuşmayabilir).")

        def _yeni_driver(_op):
            if surucu:
                return webdriver.Chrome(service=Service(executable_path=surucu),
                                        options=_op)
            return webdriver.Chrome(options=_op)

        # Chrome'u chromedriver başlatır. Önce eski oturumu düşür (takılı kalan
        # Chrome süreci "device busy"/eski sekme sorunları yaratıyor).
        _telefon_chrome_durdur(_adb_yol, cihaz_seri, log_cb)
        try:
            driver = _yeni_driver(op)
        except Exception as ex:
            m = str(ex).lower()
            if not any(s in m for s in ("not running", "unable to discover open pages",
                                        "no such window", "chrome not reachable",
                                        "devtools", "timed out")):
                raise
            _log(log_cb, "Telefon Chrome'u açılamadı, süreç düşürülüp tekrar denenecek...")
            _telefon_chrome_durdur(_adb_yol, cihaz_seri, log_cb, bekle=4)
            driver = _yeni_driver(op)
        # Gerçek dokunma katmanı için ADB bağlamını driver'a iliştir
        driver._gercek = True
        driver._adb_yol = _adb_yol
        driver._adb_seri = cihaz_seri
        try:
            driver._ekran = _adb_ekran_boyut(_adb_yol, cihaz_seri)
        except Exception:
            driver._ekran = (1080, 2400)
        # MIUI/HyperOS dokunma enjeksiyonu testi (keyevent 0 = zararsız).
        # Yasaksa tüm jestler baştan Selenium/JS'e yönlenir, takılma olmaz.
        try:
            test = _adb(_adb_yol, "shell", "input", "keyevent", "0",
                        seri=cihaz_seri, sn=8)
            if test and ("Exception" in test or "INJECT_EVENTS" in test
                         or "Error" in test):
                driver._dokunma_yok = True
                _log(log_cb, "Uyarı: telefon adb dokunma enjeksiyonunu engelliyor "
                             "(MIUI güvenlik). Selenium tıklama/kaydırma kullanılacak. "
                             "Gerçek parmak jesti istersen: Geliştirici seçenekleri > "
                             "'USB hata ayıklama (Güvenlik ayarları)' aç.")
        except Exception:
            pass
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
        _stealth_uygula(driver, mobil=mobil)

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
        # NOT: '/ncr' (No Country Redirect) KULLANILMAZ. Google'ı global/ABD
        # google.com'a sabitliyordu -> arayüz İngilizce + TR yerel reklamlar
        # açık artırmaya girmiyordu. Doğrudan .com.tr + hl/gl=tr açılır.
        _ag_bekle_ve_ac(driver, GOOGLE_URL, log_cb, iptal_mi)
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
        mouse_gezin(driver, dongu=1)

        domainler = [d.strip() for d in hedef_site.split(",") if d.strip()]
        # DB'de kayıtlı (önceki aramalarda bulunan) reklam domainlerini de HEDEF yap.
        # Kullanıcı listesi önce, sonra DB'dekiler (tekrarsız). Kara liste zaten yazılmıyor.
        try:
            # SADECE bu aramada bulunmuş domainler + elle eklenenler hedeflenir
            for d in hedef_domainler_db(arama):
                if d and d not in domainler:
                    domainler.append(d)
        except Exception:
            pass
        if domainler:
            _log(log_cb, f"  Hedef site sayısı: {len(domainler)} "
                         f"(liste + DB birleşik).")

        if sadece_reklam:
            # --- SADECE reklam (Ad/Sponsorlu) ---
            if domainler:
                # Tarama + listeyle kıyas + tek tek gir (hepsi fonksiyon içinde).
                # Her SERP dönüşünde reklamlar yeniden taranır.
                _reklamlari_sirayla_isle(driver, domainler, log_cb, serp_url,
                                         iptal_mi, arama=arama)
            else:
                # site yok: sayfayı süz, ilk N reklamı gir (yeni sekmede)
                yakalanan = _reklam_haritasi(driver, log_cb, arama, "Tarama")
                for i in range(min(tiklama, len(yakalanan))):
                    if iptal_mi():
                        break
                    dom, href = yakalanan[i]
                    _ziyaret_href(driver, href, f"[Ad] {dom[:50]}", log_cb)
                    tiklama_kaydet(dom, arama, "reklam", log_cb)
        elif domainler:
            # --- Hedef site(ler): SERP'teki gerçek sonuca TIKLA ---
            # Her domaini SERP'te taze bul, tıkla, gez, SERP'e dön, sıradakine geç.
            mouse_gezin(driver, dongu=1)
            konum_popup_kapat(driver)     # geç çıkan konum penceresini kapat
            _tum_sayfayi_kaydir(driver)   # tüm sonuç + alt reklamlar yüklensin
            # SERP'teki reklam domainlerini logla + DB'ye kaydet
            _reklam_domainleri_topla(driver, log_cb, arama)
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
                tiklama_kaydet(domain, arama,
                               "reklam" if etiket.startswith("[Ad]") else "organik",
                               log_cb)
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
                # tıklamadan önce hedef host'u al (sonra öğe stale olur)
                try:
                    _a = sonuclar[i].find_element(By.XPATH, "./ancestor::a[1]")
                    _dom = _href_host(_a.get_attribute("href") or "")
                except Exception:
                    _dom = ""
                _siteyi_gez(driver, sonuclar[i], log_cb, sonuclar[i].text[:60], serp_url)
                tiklama_kaydet(_dom, arama, "organik", log_cb)

        _log(log_cb, f"'{arama}' bitti.")

    finally:
        if gercek_telefon and driver is not None and not detach:
            # gerçek telefon: quit() sekmeleri kapatmaz -> önce sekmeleri kapat
            _telefon_sekmeleri_kapat(driver, log_cb)
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


# Windows'ta adb her çağrıda konsol (siyah pencere) açmasın -> CREATE_NO_WINDOW
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _adb(adb_yol, *args, seri=None, sn=15):
    """seri verilirse '-s <seri>' ile o cihaza yönlendir."""
    komut = [adb_yol]
    if seri:
        komut += ["-s", seri]
    komut += list(args)
    r = subprocess.run(komut, capture_output=True, text=True, timeout=sn,
                       creationflags=_NO_WINDOW)
    return (r.stdout + r.stderr).strip()


def telefon_chrome_major(adb_yol=None, seri=None):
    """Telefondaki Chrome'un ana sürüm numarası (int). Bulamazsa None."""
    adb_yol = adb_yol or adb_bul()
    try:
        cikti = _adb(adb_yol, "shell", "dumpsys", "package", "com.android.chrome",
                     seri=seri, sn=20) or ""
        for satir in cikti.splitlines():
            satir = satir.strip()
            if satir.startswith("versionName="):
                return int(satir.split("=", 1)[1].split(".")[0])
    except Exception:
        pass
    return None


def _chromedriver_indir(major, log_cb=None):
    """Verilen Chrome major sürümüne uygun chromedriver.exe indir, yolu döndür.
    %LOCALAPPDATA%\\GoogleBot\\chromedriver\\<major>\\ altına cache'ler.
    İndirilemezse None (Selenium Manager'a düşülür)."""
    import io
    import json
    import zipfile
    import urllib.request

    kok = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
                       "GoogleBot", "chromedriver", str(major))
    hedef = os.path.join(kok, "chromedriver.exe")
    if os.path.isfile(hedef):
        return hedef
    try:
        if major >= 115:
            # Chrome for Testing (yeni sürümler)
            url_v = f"https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_{major}"
            with urllib.request.urlopen(url_v, timeout=30) as r:
                surum = r.read().decode().strip()
            url_zip = (f"https://storage.googleapis.com/chrome-for-testing-public/"
                       f"{surum}/win64/chromedriver-win64.zip")
        else:
            # Eski depo (Chrome <= 114)
            url_v = f"https://chromedriver.storage.googleapis.com/LATEST_RELEASE_{major}"
            with urllib.request.urlopen(url_v, timeout=30) as r:
                surum = r.read().decode().strip()
            url_zip = f"https://chromedriver.storage.googleapis.com/{surum}/chromedriver_win32.zip"
        _log(log_cb, f"chromedriver {surum} indiriliyor (Chrome {major} için)...")
        with urllib.request.urlopen(url_zip, timeout=120) as r:
            veri = r.read()
        os.makedirs(kok, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(veri)) as z:
            for ad in z.namelist():
                if ad.endswith("chromedriver.exe"):
                    with z.open(ad) as kaynak, open(hedef, "wb") as cikis:
                        shutil.copyfileobj(kaynak, cikis)
                    break
        if os.path.isfile(hedef):
            _log(log_cb, f"chromedriver hazır: {hedef}")
            return hedef
    except Exception as ex:
        _log(log_cb, f"chromedriver indirilemedi ({str(ex)[:80]})")
    return None


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


# ---------------- Gerçek dokunma (ADB input) ----------------
# Gerçek telefonda kaydırmayı JS (window.scrollBy) yerine PARMAK hareketiyle yap.
# JS scroll: touchstart/move/end ÜRETMEZ + scrollY anlık zıplar -> Google "programatik" der.
# ADB swipe: gerçek dokunma olayları + inertia (kayma) -> gerçek kullanıcı sinyali.

def _adb_ekran_boyut(adb_yol=None, seri=None):
    """Cihaz ekran çözünürlüğü (genişlik, yükseklik) piksel. Bulamazsa (1080, 2400)."""
    adb_yol = adb_yol or adb_bul()
    try:
        cikti = _adb(adb_yol, "shell", "wm", "size", seri=seri, sn=8) or ""
        # 'Physical size: 1080x2400' ve varsa 'Override size: ...'; sonuncuyu (geçerli) al
        son = None
        for satir in cikti.splitlines():
            if "size:" in satir and "x" in satir:
                boyut = satir.split(":", 1)[1].strip()
                if "x" in boyut:
                    g, y = boyut.split("x", 1)
                    son = (int(g.strip()), int(y.strip()))
        if son:
            return son
    except Exception:
        pass
    return 1080, 2400


def _adb_swipe(x1, y1, x2, y2, sure_ms=300, adb_yol=None, seri=None):
    """Ekranda gerçek parmak kaydırması (input swipe)."""
    adb_yol = adb_yol or adb_bul()
    _adb(adb_yol, "shell", "input", "swipe",
         str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(sure_ms)),
         seri=seri, sn=10)


def _adb_kaydir_asagi(adb_yol=None, seri=None, ekran=None):
    """Sayfayı bir tık AŞAĞI kaydır: ekran ortasında YUKARI yönlü gerçek swipe."""
    w, h = ekran or _adb_ekran_boyut(adb_yol, seri)
    cx = int(w * random.uniform(0.42, 0.58))
    y1 = int(h * random.uniform(0.68, 0.78))          # parmak aşağıdan başlar
    y2 = int(h * random.uniform(0.26, 0.36))          # yukarı sürükler = sayfa aşağı iner
    _adb_swipe(cx, y1, cx + random.randint(-25, 25), y2,
               random.randint(240, 460), adb_yol, seri)


def _adb_tap(x, y, adb_yol=None, seri=None):
    """Ekranda gerçek parmak dokunuşu (input tap). x,y = cihaz pikseli.
    adb çıktısını döndürür (MIUI SecurityException tespiti için)."""
    adb_yol = adb_yol or adb_bul()
    return _adb(adb_yol, "shell", "input", "tap", str(int(x)), str(int(y)),
                seri=seri, sn=8)


def _gercek_tikla(driver, hedef, onceki_handles, onceki_url):
    """Öğeyi GERÇEK parmak dokunuşuyla (ADB input tap) tıkla.

    CSS-px öğe konumunu cihaz pikseline çevirir:
      dev_x = cx * dpr
      dev_y = cy * dpr + ust_ofset   (ust_ofset = durum çubuğu + adres çubuğu)
      ust_ofset = ekran_yukseklik - innerHeight*dpr  (adres çubuğu durumuna göre kendini düzeltir)
    Dokunuş navigasyon (URL değişimi / yeni sekme) yaratırsa True.
    Öğe görünür değilse ya da dokunuş bir şey açmazsa False -> çağıran normal tıklamaya düşer.
    """
    try:
        d = driver.execute_script(
            "const r=arguments[0].getBoundingClientRect();"
            "return {cx:r.left+r.width/2, cy:r.top+r.height/2,"
            " ih:window.innerHeight, dpr:window.devicePixelRatio||2};", hedef)
        if not d or d["cy"] < 0 or d["cy"] > d["ih"]:
            return False   # ekran dışında -> koordinat güvenilmez, normal tıkla
        w, h = getattr(driver, "_ekran", (1080, 2400))
        dpr = d["dpr"] or 2
        ust_ofset = max(0, h - d["ih"] * dpr)
        x = min(max(int(d["cx"] * dpr), 2), w - 2)
        y = min(max(int(d["cy"] * dpr + ust_ofset), 2), h - 2)
        cikti = _adb_tap(x, y, getattr(driver, "_adb_yol", None),
                         getattr(driver, "_adb_seri", None))
        if cikti and ("Exception" in cikti or "INJECT_EVENTS" in cikti
                      or "Error" in cikti):
            # MIUI: input enjeksiyonu yasak -> bir daha deneme, hep Selenium kullan
            driver._dokunma_yok = True
            return False
        # dokunuş işe yaradı mı? navigasyon / yeni sekme bekle
        for _ in range(16):
            time.sleep(0.25)
            try:
                if set(driver.window_handles) != onceki_handles:
                    return True
                if driver.current_url != onceki_url:
                    return True
            except Exception:
                return True   # sekme kapandı/değişti = tıklama işledi
        return False
    except Exception:
        return False


def _telefon_chrome_durdur(adb_yol=None, seri=None, log_cb=None, bekle=2):
    """Telefondaki Chrome sürecini düşür (force-stop).

    Chrome'u chromedriver başlatır ve androidUseRunningApp verilmediği için
    veri dizinini KENDİ temizler (her koşu temiz profil, çerez/oturum taşınmaz).
    Burada sadece takılı kalan eski süreç kapatılır; 'pm clear' çağırmıyoruz,
    onu chromedriver'a bırakıyoruz (aksi halde ilk açılış sihirbazı çıkabilir).
    """
    adb_yol = adb_yol or adb_bul()
    try:
        _adb(adb_yol, "shell", "am", "force-stop", "com.android.chrome",
             seri=seri, sn=15)
        time.sleep(bekle)
        _log(log_cb, "Telefonda eski Chrome süreci kapatıldı (temiz başlangıç).")
    except Exception as ex:
        _log(log_cb, f"Telefon Chrome kapatma uyarısı: {str(ex)[:60]}")


def _telefon_sekmeleri_kapat(driver, log_cb=None):
    """Çıkmadan önce telefondaki Chrome sekmelerini kapat.

    quit() bağlantıyı koparmadan önce gezilen sayfalar/SERP kapatılır; ekranda
    açık sayfa kalmaz. Veri zaten sonraki koşuda 'pm clear' ile silinir.
    """
    try:
        handles = list(driver.window_handles)
    except Exception:
        return
    for h in handles:
        try:
            driver.switch_to.window(h)
            driver.close()
        except Exception:
            pass
    if handles:
        _log(log_cb, f"Telefonda {len(handles)} sekme kapatıldı.")


def telefon_hazirla(adb_yol=None, seri=None, mobil_veri=True, log_cb=None):
    """Gerçek telefonu sürüşe hazırla: uyandır, kilidi aç, uyanık tut, mobil veriye geç.

    mobil_veri=True: Wi-Fi kapat + hücresel veri aç -> operatör (gerçek mobil) IP'si.
      NOT: ADB USB üzerinden olmalı. Kablosuz ADB'de Wi-Fi kapanınca bağlantı düşer.
    """
    adb_yol = adb_yol or adb_bul()
    try:
        w, h = _adb_ekran_boyut(adb_yol, seri)
        _adb(adb_yol, "shell", "input", "keyevent", "KEYCODE_WAKEUP", seri=seri, sn=8)
        time.sleep(0.4)
        # kilit ekranını yukarı kaydır (PIN yoksa açılır). Zaten açıksa zararsız.
        _adb_swipe(w // 2, int(h * 0.80), w // 2, int(h * 0.20), 250, adb_yol, seri)
        # sürüş boyunca ekran uyumasın
        _adb(adb_yol, "shell", "svc", "power", "stayon", "true", seri=seri, sn=8)
        if mobil_veri:
            _adb(adb_yol, "shell", "svc", "wifi", "disable", seri=seri, sn=8)
            _adb(adb_yol, "shell", "svc", "data", "enable", seri=seri, sn=8)
        _log(log_cb, "Telefon hazır (uyanık, kilit açık"
                     + (", mobil veri/operatör IP" if mobil_veri else "") + ").")
    except Exception as ex:
        _log(log_cb, f"Telefon hazırlama uyarısı: {str(ex)[:60]}")


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
