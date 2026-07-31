"""
Tıklama istatistikleri penceresi (tkinter, koyu tema).

Üstte gün/7 gün/30 gün toplamları, ortada gün/hafta/ay bazlı bar grafik,
altta grafikte HOVER yapılan kovanın site kırılımı tablosu.

Panelden açılır:  istatistik.pencere_ac(kok)
"""

import math
import tkinter as tk
from tkinter import ttk

import google_bot

# ---- Renk paleti (panel.py ile aynı) ----
BG        = "#0f172a"
PANEL     = "#1e293b"
PANEL2    = "#273449"
KENAR     = "#334155"
METIN     = "#e2e8f0"
SOLUK     = "#94a3b8"
ACCENT    = "#6366f1"
ACCENT_H  = "#818cf8"
YESIL     = "#22c55e"

PERIYOT_AD = {"gun": "Gün", "hafta": "Hafta", "ay": "Ay"}
TUR_AD = {"Tümü": None, "Sadece reklam": "reklam", "Sadece organik": "organik"}

# grafik iç boşlukları
PAD_L, PAD_R, PAD_T, PAD_B = 46, 14, 16, 30


class IstatistikPenceresi:
    def __init__(self, kok):
        self.kok = kok
        self.pen = tk.Toplevel(kok)
        self.pen.title("Google Bot  •  Tıklama İstatistikleri")
        self.pen.configure(bg=BG)
        sw, sh = self.pen.winfo_screenwidth(), self.pen.winfo_screenheight()
        w, h = min(1000, sw - 60), min(760, sh - 80)
        self.pen.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.pen.minsize(720, 560)

        self.periyot = "gun"
        self.tur = None
        self.seri = []          # [(anahtar, etiket, adet), ...]
        self.hover = None       # imlecin üstünde olduğu kova indeksi
        self.sabit = None       # tıklayarak sabitlenen kova indeksi
        self._gosterilen = None  # tabloda hâlen duran kova anahtarı
        self._is = None         # otomatik tazeleme timer id

        self._stil()
        self._ust()
        self._kpi()
        self._grafik_kart()
        self._tablo_kart()

        self.tazele()
        self._oto_tazele()
        self.pen.protocol("WM_DELETE_WINDOW", self.kapat)

    # ---------- yardımcılar ----------
    def _stil(self):
        """Panelle aynı koyu ttk stili (pencere tek başına açılırsa da doğru görünsün)."""
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("Trv.Treeview",
                    background=PANEL, fieldbackground=PANEL, foreground=METIN,
                    rowheight=28, borderwidth=0, font=("Segoe UI", 10))
        s.configure("Trv.Treeview.Heading",
                    background=PANEL2, foreground=SOLUK, relief="flat",
                    font=("Segoe UI Semibold", 9))
        s.map("Trv.Treeview.Heading", background=[("active", KENAR)])
        s.map("Trv.Treeview",
              background=[("selected", ACCENT)], foreground=[("selected", "white")])
        s.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                    foreground=METIN, arrowcolor=SOLUK, relief="flat",
                    bordercolor=KENAR, selectbackground=ACCENT,
                    selectforeground="white")

    def _kart(self, usta, baslik=None):
        dis = tk.Frame(usta, bg=PANEL, highlightbackground=KENAR,
                       highlightthickness=1)
        ic = tk.Frame(dis, bg=PANEL)
        ic.pack(fill="both", expand=True, padx=14, pady=12)
        if baslik:
            tk.Label(ic, text=baslik, bg=PANEL, fg=METIN,
                     font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 10))
        return dis, ic

    # ---------- üst bar ----------
    def _ust(self):
        h = tk.Frame(self.pen, bg=BG)
        h.pack(fill="x", padx=16, pady=(14, 10))
        sol = tk.Frame(h, bg=BG)
        sol.pack(side="left")
        tk.Label(sol, text="Tıklama İstatistikleri", bg=BG, fg=METIN,
                 font=("Segoe UI Semibold", 16)).pack(anchor="w")
        tk.Label(sol, text="Grafikte bir sütunun üstüne gel — alttaki tablo o "
                           "dönemin site kırılımını gösterir",
                 bg=BG, fg=SOLUK, font=("Segoe UI", 9)).pack(anchor="w")

        sag = tk.Frame(h, bg=BG)
        sag.pack(side="right")
        self.tur_kutu = ttk.Combobox(sag, state="readonly", width=15,
                                     values=list(TUR_AD.keys()))
        self.tur_kutu.current(0)
        self.tur_kutu.pack(side="left")
        self.tur_kutu.bind("<<ComboboxSelected>>", self._tur_degisti)
        b = tk.Button(sag, text="⟳", bg=PANEL2, fg=METIN, activebackground=KENAR,
                      activeforeground=METIN, relief="flat", bd=0, cursor="hand2",
                      font=("Segoe UI Semibold", 10), padx=10, pady=4,
                      command=self.tazele)
        b.pack(side="left", padx=(8, 0))

    # ---------- KPI kutuları ----------
    def _kpi(self):
        cer = tk.Frame(self.pen, bg=BG)
        cer.pack(fill="x", padx=16)
        self.kpi_lbl = {}
        for anahtar, baslik in (("bugun", "BUGÜN"), ("7", "SON 7 GÜN"),
                                ("30", "SON 30 GÜN")):
            dis, ic = self._kart(cer)
            dis.pack(side="left", fill="both", expand=True,
                     padx=(0 if anahtar == "bugun" else 12, 0))
            tk.Label(ic, text=baslik, bg=PANEL, fg=SOLUK,
                     font=("Segoe UI Semibold", 9)).pack(anchor="w")
            lbl = tk.Label(ic, text="—", bg=PANEL, fg=METIN,
                           font=("Segoe UI Semibold", 26))
            lbl.pack(anchor="w")
            tk.Label(ic, text="tıklama", bg=PANEL, fg=SOLUK,
                     font=("Segoe UI", 9)).pack(anchor="w")
            self.kpi_lbl[anahtar] = lbl

    # ---------- grafik ----------
    def _grafik_kart(self):
        dis, ic = self._kart(self.pen, None)
        dis.pack(fill="both", expand=True, padx=16, pady=(12, 0))

        ust = tk.Frame(ic, bg=PANEL)
        ust.pack(fill="x", pady=(0, 8))
        tk.Label(ust, text="Tıklama Grafiği", bg=PANEL, fg=METIN,
                 font=("Segoe UI Semibold", 11)).pack(side="left")

        self.per_btn = {}
        kutu = tk.Frame(ust, bg=PANEL)
        kutu.pack(side="right")
        for p in ("gun", "hafta", "ay"):
            b = tk.Button(kutu, text=PERIYOT_AD[p], relief="flat", bd=0,
                          cursor="hand2", font=("Segoe UI Semibold", 9),
                          padx=14, pady=5, activeforeground="white",
                          command=lambda x=p: self._periyot_sec(x))
            b.pack(side="left", padx=(6, 0))
            self.per_btn[p] = b

        self.cv = tk.Canvas(ic, bg=PANEL, highlightthickness=0, height=250)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self._ciz())
        self.cv.bind("<Motion>", self._fare)
        self.cv.bind("<Leave>", self._fare_cikti)
        self.cv.bind("<Button-1>", self._tikla)

    # ---------- tablo ----------
    def _tablo_kart(self):
        dis, ic = self._kart(self.pen, None)
        dis.pack(fill="both", expand=True, padx=16, pady=(12, 14))

        self.tablo_baslik = tk.Label(ic, text="Site kırılımı", bg=PANEL, fg=METIN,
                                     font=("Segoe UI Semibold", 11))
        self.tablo_baslik.pack(anchor="w", pady=(0, 8))

        cer = tk.Frame(ic, bg=PANEL)
        cer.pack(fill="both", expand=True)
        self.tablo = ttk.Treeview(cer, style="Trv.Treeview", height=7,
                                  columns=("site", "tik", "rek", "org"),
                                  show="headings", selectmode="browse")
        self.tablo.heading("site", text="SİTE", anchor="w")
        self.tablo.heading("tik", text="TIK")
        self.tablo.heading("rek", text="REKLAM")
        self.tablo.heading("org", text="ORGANİK")
        self.tablo.column("site", width=340, anchor="w")
        for k in ("tik", "rek", "org"):
            self.tablo.column(k, width=80, anchor="center", stretch=False)
        sb = ttk.Scrollbar(cer, orient="vertical", command=self.tablo.yview)
        self.tablo.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tablo.pack(side="left", fill="both", expand=True)

    # ---------- veri ----------
    def tazele(self):
        """DB'den her şeyi yeniden oku ve çiz."""
        self.kpi_lbl["bugun"].config(
            text=f"{google_bot.tiklama_toplam(1, self.tur):,}".replace(",", "."))
        self.kpi_lbl["7"].config(
            text=f"{google_bot.tiklama_toplam(7, self.tur):,}".replace(",", "."))
        self.kpi_lbl["30"].config(
            text=f"{google_bot.tiklama_toplam(30, self.tur):,}".replace(",", "."))
        self.seri = google_bot.tiklama_seri(self.periyot, self.tur)
        if self.sabit is not None and self.sabit >= len(self.seri):
            self.sabit = None
        self._periyot_renk()
        self._ciz()
        self._gosterilen = None          # aynı kova olsa da tabloyu tazele
        self._tablo_doldur(self._aktif_indeks())

    def _oto_tazele(self):
        """Bot çalışırken sayılar canlı artsın."""
        try:
            self.tazele()
        except Exception:
            pass
        self._is = self.pen.after(15000, self._oto_tazele)

    def _tur_degisti(self, _e=None):
        self.tur = TUR_AD.get(self.tur_kutu.get())
        self.tazele()

    def _periyot_sec(self, p):
        if p == self.periyot:
            return
        self.periyot = p
        self.hover = self.sabit = None
        self.tazele()

    def _periyot_renk(self):
        for p, b in self.per_btn.items():
            secili = (p == self.periyot)
            b.config(bg=ACCENT if secili else PANEL2,
                     fg="white" if secili else SOLUK,
                     activebackground=ACCENT_H if secili else KENAR)

    def _aktif_indeks(self):
        """Tabloda gösterilecek kova: hover > sabit > son kova."""
        if self.hover is not None:
            return self.hover
        if self.sabit is not None:
            return self.sabit
        return len(self.seri) - 1 if self.seri else None

    def _tablo_doldur(self, idx):
        if idx is None or not self.seri or idx >= len(self.seri):
            return
        anahtar, etiket, adet = self.seri[idx]
        if anahtar == self._gosterilen:
            return
        self._gosterilen = anahtar
        satirlar = google_bot.tiklama_kirilim(self.periyot, anahtar, self.tur)
        self.tablo_baslik.config(
            text=f"{self._donem_yazi(idx)} — {adet} tıklama, "
                 f"{len(satirlar)} site")
        self.tablo.delete(*self.tablo.get_children())
        for dom, n, rek, org in satirlar:
            self.tablo.insert("", "end", values=(dom, n, rek, org))
        if not satirlar:
            self.tablo.insert("", "end", values=("(kayıt yok)", "", "", ""))

    def _donem_yazi(self, idx):
        etiket = self.seri[idx][1]
        if self.periyot == "gun":
            return etiket
        if self.periyot == "hafta":
            return f"{etiket} haftası"
        return etiket

    # ---------- çizim ----------
    def _ciz(self):
        cv = self.cv
        cv.delete("all")
        W = cv.winfo_width() or 900
        H = cv.winfo_height() or 250
        n = len(self.seri)
        if n == 0 or W < 120 or H < 90:
            return

        enb = max(a for _, _, a in self.seri)
        if enb == 0:
            cv.create_text(W // 2, H // 2, fill=SOLUK, font=("Segoe UI", 11),
                           text="Bu dönemde tıklama kaydı yok.")
            return

        # y ekseni üst sınırı: 4'e bölünebilen yuvarlak sayı
        tavan = self._tavan(enb)
        x0, x1 = PAD_L, W - PAD_R
        y0, y1 = PAD_T, H - PAD_B
        plotw, ploth = x1 - x0, y1 - y0

        # ızgara + y etiketleri
        for i in range(5):
            deger = tavan * i // 4
            y = y1 - ploth * i / 4
            cv.create_line(x0, y, x1, y, fill=KENAR)
            cv.create_text(x0 - 8, y, text=str(deger), anchor="e", fill=SOLUK,
                           font=("Segoe UI", 8))

        slot = plotw / n
        bar_w = max(3, min(46, slot * 0.62))
        aktif = self._aktif_indeks()
        self._bar_kutu = []
        # x etiket seyreltme (etiket ~44px; sığmıyorsa birer atla)
        atla = max(1, int(math.ceil(44.0 / slot)))

        for i, (_anahtar, etiket, adet) in enumerate(self.seri):
            cx = x0 + slot * (i + 0.5)
            yust = y1 - (ploth * adet / tavan if tavan else 0)
            if adet > 0:
                yust = min(yust, y1 - 2)
            secili = (i == aktif)
            renk = ACCENT_H if secili else ACCENT
            if adet == 0:
                renk = KENAR
            cv.create_rectangle(cx - bar_w / 2, yust, cx + bar_w / 2, y1,
                                fill=renk, outline="")
            # sabit seçimde sayıyı sütun üstünde göster (hover'da balon zaten yazıyor)
            if secili and adet > 0 and i != self.hover:
                cv.create_text(cx, yust - 9, text=str(adet), fill=METIN,
                               font=("Segoe UI Semibold", 9))
            # en yeni kova hep yazılsın; sondan geriye doğru seyrelt
            if (n - 1 - i) % atla == 0:
                cv.create_text(cx, y1 + 12, text=etiket, fill=SOLUK,
                               font=("Segoe UI", 8))
            self._bar_kutu.append((cx - slot / 2, cx + slot / 2))

        cv.create_line(x0, y1, x1, y1, fill=SOLUK)

        # hover balonu
        if self.hover is not None and self.hover < n:
            self._balon(self.hover, x0, y0, x1, slot, ploth, y1, tavan)

    def _balon(self, i, x0, y0, x1, slot, ploth, y1, tavan):
        _anahtar, etiket, adet = self.seri[i]
        yazi = f"{self._donem_yazi(i)} • {adet} tık"
        cx = x0 + slot * (i + 0.5)
        gen = 9 + len(yazi) * 6.6
        bx0 = min(max(x0, cx - gen / 2), x1 - gen)
        by0 = max(y0, y1 - (ploth * adet / tavan if tavan else 0) - 40)
        self.cv.create_rectangle(bx0, by0, bx0 + gen, by0 + 24,
                                 fill=PANEL2, outline=KENAR)
        self.cv.create_text(bx0 + gen / 2, by0 + 12, text=yazi, fill=METIN,
                            font=("Segoe UI Semibold", 9))

    def _tavan(self, enb):
        """Y ekseni üst sınırı — 4 ızgaraya bölünen okunur yuvarlak sayı."""
        if enb <= 4:
            return 4
        ham = enb / 4.0                       # bir ızgara aralığı
        us = 10 ** math.floor(math.log10(ham))
        for c in (1, 2, 2.5, 5, 10):
            if ham <= c * us:
                return int(c * us * 4)
        return int(us * 40)

    # ---------- fare ----------
    def _indeks_bul(self, x):
        for i, (a, b) in enumerate(getattr(self, "_bar_kutu", [])):
            if a <= x < b:
                return i
        return None

    def _fare(self, e):
        idx = self._indeks_bul(e.x)
        if idx == self.hover:
            return
        self.hover = idx
        self._ciz()
        self._tablo_doldur(self._aktif_indeks())

    def _fare_cikti(self, _e=None):
        if self.hover is None:
            return
        self.hover = None
        self._ciz()
        self._tablo_doldur(self._aktif_indeks())

    def _tikla(self, e):
        idx = self._indeks_bul(e.x)
        # aynı sütuna tekrar tıklama sabiti kaldırır
        self.sabit = None if (idx is None or idx == self.sabit) else idx
        self._ciz()
        self._tablo_doldur(self._aktif_indeks())

    # ---------- kapat ----------
    def kapat(self):
        if self._is is not None:
            try:
                self.pen.after_cancel(self._is)
            except Exception:
                pass
            self._is = None
        self.pen.destroy()


_acik = {}


def pencere_ac(kok):
    """İstatistik penceresini aç (zaten açıksa öne getir)."""
    p = _acik.get("pen")
    if p is not None and p.pen.winfo_exists():
        p.pen.deiconify()
        p.pen.lift()
        p.tazele()
        return p
    p = IstatistikPenceresi(kok)
    _acik["pen"] = p
    return p
