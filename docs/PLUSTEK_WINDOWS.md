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
