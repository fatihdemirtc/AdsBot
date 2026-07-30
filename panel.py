"""
Google Bot - Profesyonel Yönetim Paneli (tkinter, koyu tema).
Aramaları + hedef siteleri yönet, ayarla, çalıştır.
Çalıştır:  python panel.py
"""

import os
import sys
import json
import time
import random
import threading
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont

import google_bot


def _temel_klasor():
    """exe yanı (frozen) ya da script klasörü. Her PC'de çalışır."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


KLASOR = _temel_klasor()
JSON_YOL = os.path.join(KLASOR, "aramalar.json")

# ---- Renk paleti (koyu, modern) ----
BG        = "#0f172a"   # ana arka plan (slate-900)
PANEL     = "#1e293b"   # kart (slate-800)
PANEL2    = "#273449"   # girişler
KENAR     = "#334155"   # çizgi
METIN     = "#e2e8f0"   # ana metin
SOLUK     = "#94a3b8"   # ikincil metin
ACCENT    = "#6366f1"   # indigo
ACCENT_H  = "#818cf8"
YESIL     = "#22c55e"
YESIL_H   = "#4ade80"
KIRMIZI   = "#ef4444"
KIRMIZI_H = "#f87171"
SARI      = "#eab308"


def yukle():
    """JSON'dan kayıtları getir. Eski format (düz string) -> dict'e çevir."""
    if os.path.exists(JSON_YOL):
        try:
            with open(JSON_YOL, "r", encoding="utf-8") as f:
                ham = json.load(f)
            sonuc = []
            for x in ham:
                if isinstance(x, str):
                    sonuc.append({"arama": x, "site": "", "tiklama": 3})
                else:
                    sonuc.append({
                        "arama": x.get("arama", ""),
                        "site": x.get("site", ""),
                        "tiklama": int(x.get("tiklama", 3)),
                    })
            return sonuc
        except Exception:
            pass
    return [
        {"arama": "python selenium tutorial", "site": "", "tiklama": 3},
        {"arama": "web scraping nedir", "site": "wikipedia.org", "tiklama": 1},
    ]


def kaydet(liste):
    with open(JSON_YOL, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=2)


class HoverButton(tk.Button):
    """Renk geçişli düz buton."""
    def __init__(self, master, renk, renk_h, **kw):
        super().__init__(
            master, bg=renk, fg="white", activebackground=renk_h,
            activeforeground="white", relief="flat", bd=0, cursor="hand2",
            font=("Segoe UI Semibold", 10), padx=14, pady=8, **kw
        )
        self._renk, self._renk_h = renk, renk_h
        self.bind("<Enter>", lambda e: self.config(bg=renk_h))
        self.bind("<Leave>", lambda e: self.config(bg=self._renk))

    def renk_ayar(self, renk, renk_h):
        self._renk, self._renk_h = renk, renk_h
        self.config(bg=renk, activebackground=renk_h)


class Panel:
    def __init__(self, kok):
        self.kok = kok
        kok.title("Google Bot  •  Yönetim Paneli")
        # Ekrana SIĞACAK şekilde boyutla (küçük ekranda taşmasın, her şey erişilebilir).
        sw, sh = kok.winfo_screenwidth(), kok.winfo_screenheight()
        w, h = min(1180, sw - 40), min(820, sh - 80)
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 3)
        kok.geometry(f"{w}x{h}+{x}+{y}")
        kok.minsize(min(820, sw - 40), min(540, sh - 80))
        kok.configure(bg=BG)

        self.calisiyor = False
        self.dur_bayrak = False

        self._stil()

        # --- Kaydırılabilir kapsayıcı: içerik ekrandan büyükse dikey scroll çıkar ---
        disk = tk.Frame(kok, bg=BG)
        disk.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(disk, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(disk, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # Tüm panel içeriği bu iç çerçeveye girer
        self.icerik = tk.Frame(self._canvas, bg=BG)
        self._ic_win = self._canvas.create_window((0, 0), window=self.icerik, anchor="nw")
        self.icerik.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(self._ic_win, width=e.width))
        # Fare tekeriyle kaydır (imleç panel üzerindeyken)
        self._canvas.bind_all("<MouseWheel>", self._tekerle_kaydir)

        self._header()

        govde = tk.Frame(self.icerik, bg=BG)
        govde.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self._sol_tablo(govde)
        self._sag_panel(govde)
        self._konsol()

        self._tabloyu_doldur(yukle())
        self._db_yenile()          # bulunan siteler (DB) tablosunu doldur
        self._kara_yenile()        # kara liste (DB'ye kaydedilmeyenler)
        self._db_oto_yenile()      # çalışırken canlı tazele
        kok.protocol("WM_DELETE_WINDOW", self.kapat)

    def _tekerle_kaydir(self, e):
        """Fare tekeri ile dikey kaydırma."""
        try:
            self._canvas.yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    # ---------- stil ----------
    def _stil(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Trv.Treeview",
                    background=PANEL, fieldbackground=PANEL, foreground=METIN,
                    rowheight=30, borderwidth=0, font=("Segoe UI", 10))
        s.configure("Trv.Treeview.Heading",
                    background=PANEL2, foreground=SOLUK, relief="flat",
                    font=("Segoe UI Semibold", 9))
        s.map("Trv.Treeview.Heading", background=[("active", KENAR)])
        s.map("Trv.Treeview",
              background=[("selected", ACCENT)], foreground=[("selected", "white")])
        s.configure("TScrollbar", background=PANEL2, troughcolor=BG,
                    bordercolor=BG, arrowcolor=SOLUK, relief="flat")
        s.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                    foreground=METIN, arrowcolor=SOLUK, relief="flat",
                    bordercolor=KENAR, selectbackground=ACCENT,
                    selectforeground="white")
        self.kok.option_add("*TCombobox*Listbox.background", PANEL2)
        self.kok.option_add("*TCombobox*Listbox.foreground", METIN)
        self.kok.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    def _kart(self, usta, baslik):
        dis = tk.Frame(usta, bg=PANEL, highlightbackground=KENAR,
                       highlightthickness=1)
        ic = tk.Frame(dis, bg=PANEL)
        ic.pack(fill="both", expand=True, padx=14, pady=12)
        if baslik:
            tk.Label(ic, text=baslik, bg=PANEL, fg=METIN,
                     font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 10))
        return dis, ic

    # ---------- header ----------
    def _header(self):
        h = tk.Frame(self.icerik, bg=BG)
        h.pack(fill="x", padx=16, pady=(14, 10))
        nokta = tk.Canvas(h, width=14, height=14, bg=BG, highlightthickness=0)
        nokta.create_oval(2, 2, 12, 12, fill=ACCENT, outline="")
        nokta.pack(side="left", padx=(0, 10))
        sol = tk.Frame(h, bg=BG)
        sol.pack(side="left")
        tk.Label(sol, text="Google Bot Yönetim Paneli", bg=BG, fg=METIN,
                 font=("Segoe UI Semibold", 16)).pack(anchor="w")
        tk.Label(sol, text="Aramaları ve hedef siteleri yönet, otomatik gez",
                 bg=BG, fg=SOLUK, font=("Segoe UI", 9)).pack(anchor="w")

        # sağ üst: durum + IP
        sag = tk.Frame(h, bg=BG)
        sag.pack(side="right")
        self.durum_lbl = tk.Label(sag, text="● Hazır", bg=BG, fg=SOLUK,
                                   font=("Segoe UI Semibold", 10))
        self.durum_lbl.pack(anchor="e")
        ipf = tk.Frame(sag, bg=BG)
        ipf.pack(anchor="e", pady=(2, 0))
        self.ip_lbl = tk.Label(ipf, text="IP: …", bg=BG, fg=SOLUK,
                               font=("Consolas", 9))
        self.ip_lbl.pack(side="left")
        HoverButton(ipf, BG, PANEL2, text="⟳",
                    command=self.ip_yenile).pack(side="left", padx=(6, 0))
        self.ip_yenile()

    # ---------- sol: arama listesi + bulunan siteler (DB) ----------
    def _sol_tablo(self, usta):
        sol = tk.Frame(usta, bg=BG)
        sol.pack(side="left", fill="both", expand=True, padx=(0, 14))

        # === Arama Listesi kartı (sadece arama kelimesi + tıklama) ===
        dis, ic = self._kart(sol, "Arama Listesi")
        dis.pack(fill="x")

        tcer = tk.Frame(ic, bg=PANEL)
        tcer.pack(fill="x")
        self.tablo = ttk.Treeview(tcer, style="Trv.Treeview", height=3,
                                  columns=("arama", "tik"),
                                  show="headings", selectmode="browse")
        self.tablo.heading("arama", text="ARAMA KELİMESİ")
        self.tablo.heading("tik", text="TIK")
        self.tablo.column("arama", width=320, anchor="w")
        self.tablo.column("tik", width=45, anchor="center")
        self.tablo.tag_configure("tek", background=PANEL)
        self.tablo.tag_configure("cift", background="#223049")
        self.tablo.pack(side="left", fill="x", expand=True)
        sb = ttk.Scrollbar(tcer, orient="vertical", command=self.tablo.yview)
        sb.pack(side="right", fill="y")
        self.tablo.configure(yscrollcommand=sb.set)
        self.tablo.bind("<<TreeviewSelect>>", self._secimi_forma)

        form = tk.Frame(ic, bg=PANEL)
        form.pack(fill="x", pady=(12, 0))
        form.columnconfigure(0, weight=1)
        self.f_arama = self._giris(form, "Arama kelimesi", 0)

        satir = tk.Frame(form, bg=PANEL)
        satir.grid(row=2, column=0, sticky="we", pady=(8, 0))
        tk.Label(satir, text="Tıklama:", bg=PANEL, fg=SOLUK,
                 font=("Segoe UI", 9)).pack(side="left")
        self.f_tik = tk.IntVar(value=3)
        tk.Spinbox(satir, from_=0, to=10, width=4, textvariable=self.f_tik,
                   bg=PANEL2, fg=METIN, buttonbackground=PANEL2, relief="flat",
                   insertbackground=METIN, highlightthickness=1,
                   highlightbackground=KENAR).pack(side="left", padx=(6, 16))
        HoverButton(satir, ACCENT, ACCENT_H, text="+ Ekle",
                    command=self.ekle).pack(side="left")
        HoverButton(satir, PANEL2, KENAR, text="Güncelle",
                    command=self.guncelle).pack(side="left", padx=6)
        HoverButton(satir, KIRMIZI, KIRMIZI_H, text="Sil",
                    command=self.sil).pack(side="left")
        HoverButton(satir, PANEL2, KENAR, text="Temizle",
                    command=self._formu_temizle).pack(side="left", padx=6)

        # === Hedef Siteler (Bulunan • DB) kartı ===
        ddis, dic = self._kart(sol, "Hedef Siteler  •  Bulunan (DB)")
        ddis.pack(fill="both", expand=True, pady=(14, 0))

        tk.Label(dic, text="Aramalarda bulunan reklam siteleri buraya otomatik "
                 "kaydedilir ve hedef olarak kullanılır. Elle de ekleyebilirsin.",
                 bg=PANEL, fg=SOLUK, font=("Segoe UI", 8), anchor="w",
                 justify="left", wraplength=470).pack(fill="x", pady=(0, 8))

        dtcer = tk.Frame(dic, bg=PANEL)
        dtcer.pack(fill="both", expand=True)
        self.db_tablo = ttk.Treeview(dtcer, style="Trv.Treeview", height=5,
                                     columns=("domain", "gor", "arama", "son"),
                                     show="headings", selectmode="browse")
        self.db_tablo.heading("domain", text="SİTE (DOMAIN)")
        self.db_tablo.heading("gor", text="GÖR")
        self.db_tablo.heading("arama", text="ARAMA")
        self.db_tablo.heading("son", text="SON GÖRÜLME")
        self.db_tablo.column("domain", width=205, anchor="w")
        self.db_tablo.column("gor", width=42, anchor="center")
        self.db_tablo.column("arama", width=140, anchor="w")
        self.db_tablo.column("son", width=135, anchor="w")
        self.db_tablo.tag_configure("tek", background=PANEL)
        self.db_tablo.tag_configure("cift", background="#223049")
        self.db_tablo.pack(side="left", fill="both", expand=True)
        dsb = ttk.Scrollbar(dtcer, orient="vertical", command=self.db_tablo.yview)
        dsb.pack(side="right", fill="y")
        self.db_tablo.configure(yscrollcommand=dsb.set)

        dgir = tk.Frame(dic, bg=PANEL)
        dgir.pack(fill="x", pady=(8, 0))
        self.f_db_giris = tk.Entry(dgir, bg=PANEL2, fg=METIN, relief="flat",
                                   insertbackground=METIN, font=("Segoe UI", 10),
                                   highlightthickness=1, highlightbackground=KENAR,
                                   highlightcolor=ACCENT)
        self.f_db_giris.pack(side="left", fill="x", expand=True, ipady=4)
        self.f_db_giris.bind("<Return>", lambda e: self._db_ekle())
        HoverButton(dgir, ACCENT, ACCENT_H, text="+ DB'ye Ekle",
                    command=self._db_ekle).pack(side="left", padx=(6, 0))
        HoverButton(dgir, KIRMIZI, KIRMIZI_H, text="Sil",
                    command=self._db_sil).pack(side="left", padx=(6, 0))
        HoverButton(dgir, PANEL2, KENAR, text="⟳ Yenile",
                    command=self._db_yenile).pack(side="left", padx=(6, 0))
        self.db_lbl = tk.Label(dgir, text="0 site", bg=PANEL, fg=SOLUK,
                               font=("Segoe UI", 8))
        self.db_lbl.pack(side="left", padx=(8, 0))

        # === Kara Liste (DB'ye kaydedilmeyenler) kartı ===
        kdis, kic = self._kart(sol, "Kara Liste  •  DB'ye Kaydedilmeyenler")
        kdis.pack(fill="x", pady=(14, 0))

        tk.Label(kic, text="Buradaki domainler (alt alanlarıyla birlikte) asla "
                 "DB'ye kaydedilmez. Eklediğinde DB'deki mevcut kayıtları da "
                 "silinir.", bg=PANEL, fg=SOLUK, font=("Segoe UI", 8),
                 anchor="w", justify="left", wraplength=470).pack(fill="x",
                                                                  pady=(0, 8))

        ktcer = tk.Frame(kic, bg=PANEL)
        ktcer.pack(fill="x")
        self.kara_tablo = ttk.Treeview(ktcer, style="Trv.Treeview", height=4,
                                       columns=("domain", "eklenme"),
                                       show="headings", selectmode="browse")
        self.kara_tablo.heading("domain", text="DOMAIN")
        self.kara_tablo.heading("eklenme", text="EKLENME")
        self.kara_tablo.column("domain", width=290, anchor="w")
        self.kara_tablo.column("eklenme", width=150, anchor="w")
        self.kara_tablo.tag_configure("tek", background=PANEL)
        self.kara_tablo.tag_configure("cift", background="#223049")
        self.kara_tablo.pack(side="left", fill="x", expand=True)
        ksb = ttk.Scrollbar(ktcer, orient="vertical",
                            command=self.kara_tablo.yview)
        ksb.pack(side="right", fill="y")
        self.kara_tablo.configure(yscrollcommand=ksb.set)

        kgir = tk.Frame(kic, bg=PANEL)
        kgir.pack(fill="x", pady=(8, 0))
        self.f_kara_giris = tk.Entry(kgir, bg=PANEL2, fg=METIN, relief="flat",
                                     insertbackground=METIN,
                                     font=("Segoe UI", 10),
                                     highlightthickness=1,
                                     highlightbackground=KENAR,
                                     highlightcolor=ACCENT)
        self.f_kara_giris.pack(side="left", fill="x", expand=True, ipady=4)
        self.f_kara_giris.bind("<Return>", lambda e: self._kara_ekle())
        HoverButton(kgir, ACCENT, ACCENT_H, text="+ Ekle",
                    command=self._kara_ekle).pack(side="left", padx=(6, 0))
        HoverButton(kgir, KIRMIZI, KIRMIZI_H, text="Sil",
                    command=self._kara_sil).pack(side="left", padx=(6, 0))
        self.kara_lbl = tk.Label(kgir, text="0 domain", bg=PANEL, fg=SOLUK,
                                 font=("Segoe UI", 8))
        self.kara_lbl.pack(side="left", padx=(8, 0))

    def _giris(self, usta, etiket, satir):
        tk.Label(usta, text=etiket, bg=PANEL, fg=SOLUK,
                 font=("Segoe UI", 9)).grid(row=satir * 2, column=0, sticky="w",
                                            pady=(6, 2))
        e = tk.Entry(usta, bg=PANEL2, fg=METIN, relief="flat",
                     insertbackground=METIN, font=("Segoe UI", 10),
                     highlightthickness=1, highlightbackground=KENAR,
                     highlightcolor=ACCENT)
        e.grid(row=satir * 2 + 1, column=0, sticky="we", ipady=5)
        return e

    # ---------- sağ: ayarlar + kontrol ----------
    def _sag_panel(self, usta):
        sag = tk.Frame(usta, bg=BG, width=260)
        sag.pack(side="left", fill="y")
        sag.pack_propagate(False)

        dis, ic = self._kart(sag, "Ayarlar")
        dis.pack(fill="x")

        # Sürekli tekrar (sonsuz) + Sadece reklam (Ad) DAİMA açık — kullanıcı seçmez.
        self.headless = tk.BooleanVar(value=False)
        self._ayar_check(ic, "Görünmez mod (headless)", self.headless)
        self.mobil = tk.BooleanVar(value=False)
        self._ayar_check(ic, "Mobil ekran (telefon görünümü)", self.mobil)
        self.gercek_telefon = tk.BooleanVar(value=False)
        self._ayar_check(ic, "Gerçek telefon (ADB Android Chrome)", self.gercek_telefon)
        self.detach = tk.BooleanVar(value=False)
        self._ayar_check(ic, "Tarayıcı açık kalsın", self.detach)
        self.sadece_secili = tk.BooleanVar(value=False)
        self._ayar_check(ic, "Sadece seçili satır", self.sadece_secili)

        # ADB cihaz seçimi
        df = tk.Frame(ic, bg=PANEL)
        df.pack(fill="x", pady=(8, 2))
        ust = tk.Frame(df, bg=PANEL)
        ust.pack(fill="x")
        tk.Label(ust, text="Android cihaz:", bg=PANEL, fg=SOLUK,
                 font=("Segoe UI", 9)).pack(side="left")
        HoverButton(ust, PANEL2, KENAR, text="⟳",
                    command=self.cihaz_yenile).pack(side="right")
        self.cihaz = tk.StringVar(value="(otomatik)")
        self.cihaz_cb = ttk.Combobox(df, textvariable=self.cihaz, state="readonly",
                                     font=("Segoe UI", 9), values=["(otomatik)"])
        self.cihaz_cb.pack(fill="x", pady=(3, 0))

        # Bitince uçak modu aç/kapa (IP yenile)
        self.ucak_yenile_var = tk.BooleanVar(value=True)
        self._ayar_check(ic, "Bitince uçak modu kapat-aç (IP yenile)",
                         self.ucak_yenile_var)

        self.cihaz_yenile()

        kdis, kic = self._kart(sag, "")
        kdis.pack(fill="x", pady=(14, 0))
        self.baslat_btn = HoverButton(kic, YESIL, YESIL_H, text="▶  BAŞLAT",
                                      command=self.baslat)
        self.baslat_btn.config(font=("Segoe UI Semibold", 12), pady=11)
        self.baslat_btn.pack(fill="x")
        self.dur_btn = HoverButton(kic, KIRMIZI, KIRMIZI_H, text="■  DURDUR",
                                   command=self.durdur, state="disabled")
        self.dur_btn.pack(fill="x", pady=(8, 0))

    def _ayar_spin(self, usta, etiket, varsayilan, a, b):
        f = tk.Frame(usta, bg=PANEL)
        f.pack(fill="x", pady=4)
        tk.Label(f, text=etiket, bg=PANEL, fg=SOLUK,
                 font=("Segoe UI", 9)).pack(side="left")
        v = tk.IntVar(value=varsayilan)
        tk.Spinbox(f, from_=a, to=b, width=4, textvariable=v, bg=PANEL2, fg=METIN,
                   buttonbackground=PANEL2, relief="flat", insertbackground=METIN,
                   highlightthickness=1, highlightbackground=KENAR).pack(side="right")
        return v

    def _ayar_check(self, usta, etiket, var):
        c = tk.Checkbutton(usta, text=etiket, variable=var, bg=PANEL, fg=METIN,
                           selectcolor=PANEL2, activebackground=PANEL,
                           activeforeground=METIN, font=("Segoe UI", 9),
                           anchor="w", relief="flat", highlightthickness=0,
                           cursor="hand2")
        c.pack(fill="x", pady=2)

    # ---------- konsol ----------
    def _konsol(self):
        dis = tk.Frame(self.icerik, bg="#0b1120", highlightbackground=KENAR,
                       highlightthickness=1)
        dis.pack(fill="both", expand=False, padx=16, pady=(0, 14))
        ust = tk.Frame(dis, bg="#0b1120")
        ust.pack(fill="x", padx=10, pady=(6, 0))
        tk.Label(ust, text="GÜNLÜK", bg="#0b1120", fg=SOLUK,
                 font=("Segoe UI Semibold", 8)).pack(side="left")
        HoverButton(ust, "#0b1120", PANEL2, text="temizle",
                    command=self._log_temizle).pack(side="right")
        self.log = tk.Text(dis, height=8, bg="#0b1120", fg="#cbd5e1",
                           insertbackground=METIN, relief="flat", wrap="word",
                           font=("Consolas", 9), state="disabled")
        self.log.pack(fill="both", expand=True, padx=10, pady=(2, 8))
        self.log.tag_configure("hata", foreground=KIRMIZI_H)
        self.log.tag_configure("ok", foreground=YESIL_H)

    def _log_temizle(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    # ---------- tablo veri ----------
    def _tabloyu_doldur(self, liste):
        for i in self.tablo.get_children():
            self.tablo.delete(i)
        for idx, x in enumerate(liste):
            tag = "cift" if idx % 2 else "tek"
            self.tablo.insert("", "end", tags=(tag,),
                              values=(x["arama"], x["tiklama"]))

    def _veriyi_al(self):
        veri = []
        for iid in self.tablo.get_children():
            a, t = self.tablo.item(iid, "values")
            # site artık DB'den geliyor -> boş (JSON uyumu için alan korunur)
            veri.append({"arama": a, "site": "", "tiklama": int(t)})
        return veri

    def _renkleri_tazele(self):
        for idx, iid in enumerate(self.tablo.get_children()):
            self.tablo.item(iid, tags=("cift" if idx % 2 else "tek",))

    def _kaydet(self):
        kaydet(self._veriyi_al())

    # ---- hedef siteler (DB) ----
    def _db_yenile(self):
        """DB'deki bulunan reklam sitelerini tabloya doldur."""
        try:
            satirlar = google_bot.reklam_domainleri_listele()
        except Exception:
            satirlar = []
        sec = self.db_tablo.selection()
        secili = self.db_tablo.item(sec[0], "values")[0] if sec else None
        for i in self.db_tablo.get_children():
            self.db_tablo.delete(i)
        for idx, r in enumerate(satirlar):
            domain, sayi, ilk, son, arama = r
            tag = "cift" if idx % 2 else "tek"
            iid = self.db_tablo.insert("", "end", tags=(tag,),
                                       values=(domain, sayi, arama or "", son or ""))
            if domain == secili:
                self.db_tablo.selection_set(iid)
        self.db_lbl.config(text=f"{len(satirlar)} site")

    def _db_ekle(self):
        """Elle domain ekle -> DB'ye yaz (hedeflerle senkron)."""
        d = self.f_db_giris.get().strip()
        if not d:
            return
        try:
            yeni = google_bot.reklam_domain_kaydet([d], "manuel", self.yaz)
        except Exception as ex:
            self.yaz(f"DB ekleme hatası: {ex}", "hata")
            yeni = []
        self.f_db_giris.delete(0, "end")
        if not yeni:
            messagebox.showinfo(
                "Bilgi", f"'{d}' eklenmedi.\nGeçersiz domain, kara listede "
                         f"ya da zaten kayıtlı olabilir.")
        self._db_yenile()

    def _db_sil(self):
        sec = self.db_tablo.selection()
        if not sec:
            return
        domain = self.db_tablo.item(sec[0], "values")[0]
        if not messagebox.askyesno("Sil", f"'{domain}' DB'den silinsin mi?"):
            return
        try:
            google_bot.reklam_domain_sil(domain)
        except Exception as ex:
            self.yaz(f"DB sil hatası: {ex}", "hata")
        self._db_yenile()

    # ---- kara liste (DB'ye kaydedilmeyenler) ----
    def _kara_yenile(self):
        """Kara liste tablosunu DB'den doldur."""
        try:
            satirlar = google_bot.engelli_listele()
        except Exception:
            satirlar = []
        for i in self.kara_tablo.get_children():
            self.kara_tablo.delete(i)
        for idx, (domain, eklenme) in enumerate(satirlar):
            tag = "cift" if idx % 2 else "tek"
            self.kara_tablo.insert("", "end", tags=(tag,),
                                   values=(domain, eklenme or ""))
        self.kara_lbl.config(text=f"{len(satirlar)} domain")

    def _kara_ekle(self):
        d = self.f_kara_giris.get().strip()
        if not d:
            return
        try:
            eklendi = google_bot.engelli_ekle(d)
        except Exception as ex:
            self.yaz(f"Kara liste ekleme hatası: {ex}", "hata")
            eklendi = False
        if eklendi:
            self.f_kara_giris.delete(0, "end")
            self.yaz(f"Kara listeye eklendi: {d}", "ok")
        else:
            messagebox.showinfo(
                "Bilgi", f"'{d}' eklenmedi.\nGeçersiz domain ya da zaten "
                         f"kara listede olabilir.")
        self._kara_yenile()
        self._db_yenile()   # eşleşen hedef kayıtlar silinmiş olabilir

    def _kara_sil(self):
        sec = self.kara_tablo.selection()
        if not sec:
            return
        domain = self.kara_tablo.item(sec[0], "values")[0]
        if not messagebox.askyesno(
                "Sil", f"'{domain}' kara listeden çıkarılsın mı?\n"
                       f"Bundan sonra aramalarda görülürse DB'ye kaydedilir."):
            return
        try:
            google_bot.engelli_sil(domain)
        except Exception as ex:
            self.yaz(f"Kara liste silme hatası: {ex}", "hata")
        self._kara_yenile()

    def _db_oto_yenile(self):
        """Çalışırken bulunan yeni siteler canlı görünsün (periyodik tazele)."""
        if self.calisiyor:
            self._db_yenile()
        self.kok.after(4000, self._db_oto_yenile)

    def _secimi_forma(self, e=None):
        sec = self.tablo.selection()
        if not sec:
            return
        a, t = self.tablo.item(sec[0], "values")
        self._formu_temizle()
        self.f_arama.insert(0, a)
        self.f_tik.set(int(t))

    def _formu_temizle(self):
        self.f_arama.delete(0, "end")
        self.f_tik.set(3)

    # ---------- buton işlemleri ----------
    def ekle(self):
        a = self.f_arama.get().strip()
        if not a:
            messagebox.showwarning("Uyarı", "Arama kelimesi boş olamaz.")
            return
        idx = len(self.tablo.get_children())
        self.tablo.insert("", "end", tags=("cift" if idx % 2 else "tek",),
                          values=(a, self.f_tik.get()))
        self._kaydet()
        self._formu_temizle()

    def guncelle(self):
        sec = self.tablo.selection()
        if not sec:
            messagebox.showinfo("Bilgi", "Önce tablodan satır seç.")
            return
        a = self.f_arama.get().strip()
        if not a:
            return
        self.tablo.item(sec[0], values=(a, self.f_tik.get()))
        self._kaydet()

    def sil(self):
        sec = self.tablo.selection()
        if not sec:
            return
        self.tablo.delete(sec[0])
        self._renkleri_tazele()
        self._kaydet()
        self._formu_temizle()

    # ---------- log ----------
    def yaz(self, mesaj, tag=None):
        def _ekle():
            self.log.config(state="normal")
            self.log.insert("end", mesaj + "\n", tag or "")
            self.log.see("end")
            self.log.config(state="disabled")
        self.kok.after(0, _ekle)

    def _durum(self, metin, renk):
        self.kok.after(0, lambda: self.durum_lbl.config(text="● " + metin, fg=renk))

    # ---------- public IP ----------
    def ip_yenile(self):
        """Public IP'yi arka planda çek, sağ üstte göster."""
        self.kok.after(0, lambda: self.ip_lbl.config(text="IP: …", fg=SOLUK))

        def _calis():
            ip = self._public_ip_al()
            renk = METIN if ip else KIRMIZI_H
            metin = f"IP: {ip}" if ip else "IP: alınamadı"
            self.kok.after(0, lambda: self.ip_lbl.config(text=metin, fg=renk))

        threading.Thread(target=_calis, daemon=True).start()

    def _public_ip_al(self):
        for url in ("https://api.ipify.org", "https://ifconfig.me/ip",
                    "https://icanhazip.com"):
            try:
                with urllib.request.urlopen(url, timeout=6) as r:
                    ip = r.read().decode("utf-8", "ignore").strip()
                    if ip and len(ip) <= 45:
                        return ip
            except Exception:
                continue
        return None

    # ---------- çalıştırma ----------
    def baslat(self):
        if self.calisiyor:
            return
        veri = self._veriyi_al()
        if self.sadece_secili.get():
            sec = self.tablo.selection()
            if sec:
                idx = self.tablo.index(sec[0])
                veri = [veri[idx]]
        if not veri:
            messagebox.showwarning("Uyarı", "Çalıştırılacak arama yok.")
            return

        self.calisiyor = True
        self.dur_bayrak = False
        self.baslat_btn.config(state="disabled")
        self.dur_btn.config(state="normal")
        self._durum("Çalışıyor", SARI)

        threading.Thread(target=self._calistir, args=(veri,), daemon=True).start()

    def _calistir(self, veri):
        sonsuz = True          # daima sürekli tekrar (durana dek)
        reklam = True          # daima sadece reklam (Ad) linkleri
        detach = self.detach.get()
        headless = self.headless.get()
        mobil = self.mobil.get()
        gercek_telefon = self.gercek_telefon.get()
        cihaz_seri = self._secili_seri()
        tur = 0
        arama_sayaci = 0
        # kaç aramada bir uzun 'oturum molası' verilecek (bot-önleme)
        sonraki_mola = random.randint(5, 8)
        try:
            while not self.dur_bayrak:
                tur += 1
                self.yaz(f"═══ Tur {tur} (sürekli) ═══")

                for i, x in enumerate(veri):
                    if self.dur_bayrak:
                        break
                    try:
                        google_bot.run_bot(
                            x["arama"],
                            hedef_site="",   # hedefler DB'den gelir (senkron)
                            tiklama=x["tiklama"],
                            detach=detach,
                            gorunmez=headless,
                            sadece_reklam=reklam,
                            log_cb=self.yaz,
                            dur_kontrol=lambda: self.dur_bayrak,
                            mobil=mobil,
                            gercek_telefon=gercek_telefon,
                            cihaz_seri=cihaz_seri,
                        )
                    except Exception as ex:
                        self.yaz(f"HATA ({x['arama']}): {ex}", "hata")

                    arama_sayaci += 1
                    if self.dur_bayrak:
                        break

                    son_arama = (i == len(veri) - 1)
                    if arama_sayaci >= sonraki_mola:
                        # --- oturum sınırı: uzun soğuma + IP yenile ---
                        arama_sayaci = 0
                        sonraki_mola = random.randint(5, 8)
                        mola = random.uniform(180, 360)   # 3-6 dk
                        self.yaz(f"⏸ Oturum molası: {int(mola)} sn "
                                 f"(bot-önleme, IP yenileniyor)...")
                        self._ucak_uygula()               # IP değiştir
                        self._bekle_kesintili(mola)
                    elif not son_arama:
                        # --- aramalar arası DEĞİŞKEN kısa aralık ---
                        ara = random.uniform(20, 75)
                        if random.random() < 0.15:
                            ara += random.uniform(60, 150)   # ara sıra uzun duraklama
                        self.yaz(f"⏱ Sonraki aramaya {int(ara)} sn...")
                        self._bekle_kesintili(ara)

                if self.dur_bayrak:
                    break
                # her turun sonunda uçak modu kapat-aç (IP yenile)
                self._ucak_uygula()
        finally:
            self.yaz("✓ Durduruldu." if self.dur_bayrak else "✓ Tamamlandı.", "ok")
            self.kok.after(0, self._bitti)

    def _bekle_kesintili(self, saniye):
        """saniye kadar bekle ama 'Durdur'a basılınca (dur_bayrak) hemen çık."""
        gecen = 0.0
        while gecen < saniye and not self.dur_bayrak:
            time.sleep(0.5)
            gecen += 0.5

    def cihaz_yenile(self):
        """ADB cihazlarını tara, combobox'ı doldur."""
        try:
            cihazlar = google_bot.adb_cihazlar()
        except Exception as ex:
            cihazlar = []
            self.yaz(f"ADB cihaz okunamadı: {ex}", "hata")
        self._cihaz_map = {}
        secenekler = ["(otomatik)"]
        for c in cihazlar:
            etiket = f"{c['model'] or c['seri']}  [{c['seri']}]  {c['durum']}"
            secenekler.append(etiket)
            self._cihaz_map[etiket] = c["seri"]
        self.cihaz_cb.config(values=secenekler)
        if self.cihaz.get() not in secenekler:
            self.cihaz.set("(otomatik)")
        if hasattr(self, "log"):
            self.yaz(f"ADB: {len(cihazlar)} cihaz bulundu.")

    def _secili_seri(self):
        secim = self.cihaz.get()
        if secim == "(otomatik)":
            return None
        return self._cihaz_map.get(secim)

    def _ucak_uygula(self):
        if not self.ucak_yenile_var.get():
            return
        seri = self._secili_seri()
        self.yaz(f"Uçak modu AÇ, 15 sn bekle, KAPA... (cihaz: {seri or 'otomatik'})")
        try:
            google_bot.ucak_modu_yenile(seri=seri, bekle=15, geri_bekle=12,
                                        log_cb=self.yaz)
        except Exception as ex:
            self.yaz(f"Uçak modu HATA: {ex}", "hata")
        # IP değişti mi göster
        self.ip_yenile()

    def _bitti(self):
        self.calisiyor = False
        self.baslat_btn.config(state="normal")
        self.dur_btn.config(state="disabled")
        self._durum("Hazır", SOLUK)

    def durdur(self):
        self.dur_bayrak = True
        self.yaz("Durduruluyor... mevcut adım bitince duracak.")
        self._durum("Durduruluyor", KIRMIZI_H)

    def kapat(self):
        self.dur_bayrak = True
        self._kaydet()
        self.kok.destroy()


if __name__ == "__main__":
    try:
        kok = tk.Tk()
        Panel(kok)
        kok.mainloop()
    except Exception:
        # Sessiz çökmeyi yakala: log dosyası + hata kutusu (windowed exe'de görünmez olur)
        import traceback
        iz = traceback.format_exc()
        try:
            with open(os.path.join(KLASOR, "hata_log.txt"), "w",
                      encoding="utf-8") as f:
                f.write(iz)
        except Exception:
            pass
        try:
            from tkinter import messagebox as _mb
            _mb.showerror("GoogleBot - Başlatma Hatası", iz[-1500:])
        except Exception:
            pass
        raise
