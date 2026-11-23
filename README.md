# AICADBridge Link — host plugins

## Kurulum (Windows)

1. [AICADBridgeSetup.exe](https://github.com/Bicim/aicadbridge-connectors/releases/latest/download/AICADBridgeSetup.exe) dosyasını indir.
2. Çift tıkla. **Hepsini kur ve bağla**'ya bas (bilgisayardaki Fusion / Blender / SolidWorks / Inventor / Solid Edge / CATIA / AutoCAD / Rhino otomatik algılanır; "STEP klasörü" her zaman seçilebilir).
3. Açılan tarayıcıda pencerede görünen kodu onayla. Fusion'u yeniden başlat; araç çubuğunda **AICADBridge** düğmesi görünür.

SmartScreen uyarısı çıkarsa: **Daha fazla bilgi → Yine de çalıştır** (exe henüz imzalı değil).
Fusion'da düğmeyi bulamazsan: Shift+S → Eklentiler → AICADBridgeLink → Çalıştır.

Web'de (AICADBridge Studio) üretilen parametrik modelleri CAD programlarına aktaran ince eklentiler.
Tüm iş mantığı bulutta; eklentiler yalnızca **dışarı doğru HTTPS** kullanır (port açmaz).

## Desteklenen programlar

| Program | Durum | Klasör | Model nasıl açılır |
|---|---|---|---|
| Autodesk Fusion | Hazır | `fusion/AICADBridgeLink` | Eklenti, STEP → yeni bileşen. Araç çubuğu: **AICADBridge Bağlan** |
| Blender 4.2+ | Hazır | `blender/aicadbridge_link` | Eklenti, GLB → yeni koleksiyon. N paneli › AICADBridge › Bağlan |
| SolidWorks 2021+ | Hazır | `desktop` | COM (`SldWorks.Application`), `LoadFile4` ile STEP açar |
| Autodesk Inventor | Beta | `desktop` | COM (`Inventor.Application`), `Documents.Open(step, True)` |
| Siemens Solid Edge | Beta | `desktop` | COM (`SolidEdge.Application`), `Documents.Open(step)` |
| CATIA V5 | Beta | `desktop` | COM (`CATIA.Application`), `Documents.Open(step)` |
| AutoCAD (2022+) | Beta | `desktop` | COM (`AutoCAD.Application`), açık çizime `_-IMPORT "<step>"` gönderir (çizim yoksa yenisini açar) |
| Rhino 8 | Beta | `rhino` | Rhino içinde çalışan betik; arka plan iş parçacığı bekler, STEP'i arayüz iş parçacığında `RhinoDoc.Import` ile alır |
| STEP klasörü (NX, Creo, Ansys, Abaqus, COMSOL, STAR-CCM+, HyperMesh…) | STEP klasörü | `desktop` | STEP `~/AICADBridge/inbox/<ad>.step` olarak kaydedilir, klasör Gezgin'de açılır; dosyayı programa elle içe aktarırsın |

COM köprüleri (`desktop/aicadbridge_desktop.py --host <solidworks|inventor|solidedge|catia|autocad|other>`):
çalışan programa `GetActiveObject`, yoksa `Dispatch` ile bağlanır. `pywin32` yoksa, program bulunamazsa ya da içe aktarma
hata verirse STEP yine `~/AICADBridge/inbox` klasörüne kaydedilir ve iş "tamam" + dosyanın yeri mesajıyla bildirilir.
`AICADBridgeSetup.exe` bu köprüyü kendi içinde taşır (`AICADBridgeSetup.exe --bridge <host>`), masaüstündeki
**AICADBridge <Program>** kısayolu onu arka planda başlatır: kullanıcı PC'sinde Python gerekmez.
Elle kurulum: zip'i aç → `run.bat <host>` (pywin32'yi kurar). Rhino: `RunPythonScript` ile `AICADBridgeLink.py` dosyasını çalıştır.

Akış: eklenti `POST /v1/connect/start` ile kod alır → kullanıcı web'de **Entegrasyonlar** sayfasında onaylar →
eklenti cihaz anahtarını alır (`~/.aicadbridge/<host>.json`) → `GET /v1/host/jobs/next` uzun yoklama → içe aktar → sonuç bildir.
Web'den bağlantı kaldırılınca anahtar bir sonraki çağrıda geçersizleşir ve silinir.

Sunucu adresi: varsayılan `https://aicad.92-5-107-17.sslip.io`. Blender eklenti tercihlerinde,
masaüstü köprüsünde `--server` ile; ya da `AICAD_API_URL` ortam değişkeni.

## Eklenti zip'leri (elle kurulum)

Her sürümde `AICADBridgeSetup.exe` ile birlikte yayınlanır (`releases/latest/download/<ad>`):
`AICADBridgeLink-fusion.zip`, `aicadbridge_link-blender.zip`, `AICADBridgeLink-desktop.zip` (SolidWorks, Inventor, Solid Edge, CATIA, AutoCAD, STEP klasörü),
`AICADBridgeLink-rhino.zip`; `AICADBridgeLink-solidworks.zip` eski bağlantılar için `desktop` ile aynıdır. Hepsi canlı sunucu varsayılanlı `aicad_link.py` içerir.

```
python tools/bundle.py [ÇIKIŞ_KLASÖRÜ]   # zip'leri üretir (varsayılan ../aicadbridge-web/downloads)
python -m pytest -q tests                # test_link.py canlı API ister (yoksa atlanır)
```

Lisans: `blender/` altındaki eklenti (ve onunla dağıtılan `aicad_link.py` kopyası) GPL-3.0-or-later, bkz. `blender/aicadbridge_link/LICENSE`. Diğer dosyalar için lisans henüz belirlenmedi.
