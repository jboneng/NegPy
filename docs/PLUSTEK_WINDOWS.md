# Windows USB setup (Plustek OpticFilm)

NegPy’s in-tree Plustek USB driver talks to the OpticFilm **8200i SE** over raw USB (libusb via PyUSB). The stock Plustek Windows driver must not own the device.

## Requirements

- Windows 10/11
- OpticFilm **8200i SE** (`07B3:1825`, GL128) — the only model validated for scan
- WinUSB (or libusbK) bound via [Zadig](https://zadig.akeo.ie/)
- From a NegPy source checkout: `uv sync --group plustek` (pulls `pyusb` and `libusb-package`)

## 1. Confirm the device

With the scanner powered and plugged in:

1. Open **Device Manager**
2. Look under imaging / USB devices for Plustek
3. Properties → Details → Hardware Ids should include `VID_07B3&PID_1825`

## 2. Bind WinUSB (Zadig)

1. Download [Zadig](https://zadig.akeo.ie/)
2. **Options → List All Devices**
3. Select the Plustek Film Scanner (`07B3:1825`)
4. Replace the driver with **WinUSB** (libusbK also works)
5. Keep the stock Plustek driver installer available if you need to restore VueScan/vendor software later

While WinUSB is bound, Plustek’s stock Windows scanning apps will not see the device.

## 3. Install USB support in NegPy

```powershell
cd path\to\NegPy
uv sync --group plustek
make run
```

In the Scan tab, Backend should be **Plustek (USB)**. Refresh the device list; the SE should appear when WinUSB is bound.

## 4. Restoring the vendor driver

1. Unplug the scanner
2. Device Manager → uninstall the WinUSB device (check “delete driver software” if offered)
3. Reinstall the Plustek / VueScan driver package
4. Replug the scanner

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Empty device list | Wrong PID, unplugged, or still on vendor driver |
| `DriverBindingError` / access denied | WinUSB not bound; another app has the handle |
| `UsbError` / link failures | Cable/hub; try a direct motherboard port |
| Missing PyUSB hint in the UI | Run `uv sync --group plustek` |
| First scan at a DPI takes a few seconds | Normal — AFE + one dark + one white shading strip (same choreography as SilverFast), then cached per resolution |
| ASIC shading ready (preferred) | Log shows colour white mean ~11–13k after unity, then `ASIC shading ready`, image `shading=True` / DVDSET on. Delete `plustek_calib` and rescan after driver changes so calib is remeasured |
| White strip still raw (~55k) after DVDSET | HW post-unity reshape did not run — host path used. Check white START (`0x01=0x23`, `0x02` has `MTRPWR`, dpi clocks). Do not arm fake ~12k whites |
| Carriage not at home after shading / scan abort | White strip must park via `AGOHOME` after SCAN clear. If park timed out, use SilverFast or power-cycle, then retry |
| Darker corners than SilverFast on Full window | Full window includes ~0.8 mm holder chrome; shading is per-column only, so residual Y falloff at those edges is expected vs a tighter SilverFast frame |
| Positive very dark until you crop (white edges on negative) | Holder chrome is brighter than film base; NegPy auto bounds latch onto it. Host-path scans clamp border highlights to the film inset (`host calib border highlight clamp…`). Also raise Process → **Analysis Buffer**, or crop before auto |
| Rainbow vertical “barcode” stripes | ASIC `DVDSET` armed with whites that were not HW post-unity (raw ~55k, or host-fake ~12k). Leave DVDSET off; use host dark/white stretch until HW white ~12k. Delete `plustek_calib` (cache v9+) and rescan |
| Diamond / sheared scene (objects lean) | Image X was shrunk to the AHB shading width while USB still paced the full line. Full window @1800 must stay ~2592 px; pad the shading table up. Delete `plustek_calib` (cache v9+) and rescan — log should show `pixels=2592` |
| Negative very dark / positive washed bright (vs SilverFast) | Host path stretched to *home* chrome white while the film window is dimmer. Log may show `host calib exposure makeup gain=…`. Prefer HW whites when available; otherwise rescan with makeup + border clamp |
| Scan fails: white mean > 20000 | Only when host fallback cannot run. Log should prefer host stretch when `host_unity_preview` ~12k; check `0x01` START=`0x23` on the white strip |
| Scan fails: white≈dark / DVDSET span | Often a stale AHB strip (wait until buffer has data **at home**; motor-busy `0xa5` is not ready). Also check lamp actually off and head on clear home chrome. Cache v6+ ignores collapsed calib |
| Scan fails: colour ASIC shading / clear home field | Carriage not on the clear home sensor; park/power-cycle, then retry (film may stay loaded) |
