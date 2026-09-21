"""
BatteryChime - NVDA Battery Warning Addon
Author: Leo
Version: 1.1.0

Plays custom sounds when battery reaches low, critical, and emergency
levels, and when the charger is plugged in, unplugged, or the battery
becomes fully charged.
"""

import globalPluginHandler
import addonHandler
import gui
import config
import wx
import os
import threading
import time
import nvwave

addonHandler.initTranslation()

confspec = {
    "lowEnabled": "boolean(default=True)",
    "lowPercent": "integer(default=20, min=5, max=95)",
    "lowMode": "string(default='pack')",
    "lowPackSound": "string(default='soft')",
    "lowCustomPath": "string(default='')",

    "criticalEnabled": "boolean(default=True)",
    "criticalPercent": "integer(default=10, min=2, max=30)",
    "criticalMode": "string(default='pack')",
    "criticalPackSound": "string(default='dramatic')",
    "criticalCustomPath": "string(default='')",

    "emergencyEnabled": "boolean(default=True)",
    "emergencyPercent": "integer(default=5, min=1, max=15)",
    "emergencyMode": "string(default='pack')",
    "emergencyPackSound": "string(default='horror')",
    "emergencyCustomPath": "string(default='')",

    "pluggedInEnabled": "boolean(default=True)",
    "pluggedInMode": "string(default='pack')",
    "pluggedInPackSound": "string(default='plugin1')",
    "pluggedInCustomPath": "string(default='')",

    "unpluggedEnabled": "boolean(default=True)",
    "unpluggedMode": "string(default='pack')",
    "unpluggedPackSound": "string(default='unplug')",
    "unpluggedCustomPath": "string(default='')",

    "fullyChargedEnabled": "boolean(default=True)",
    "fullyChargedPercent": "integer(default=100, min=90, max=100)",
    "fullyChargedMode": "string(default='pack')",
    "fullyChargedPackSound": "string(default='charged1')",
    "fullyChargedCustomPath": "string(default='')",

    "checkInterval": "integer(default=60, min=10, max=300)",
}
config.conf.spec["BatteryChime"] = confspec

SOUND_PACK = {
    "chime": "Classic Chime",
    "retro": "Retro Beep",
    "soft": "Soft Bell",
    "dramatic": "Dramatic Hit",
    "horror": "Horror Sting",
    "chill": "Chill Tone",
    "plugin1": "Network Connect",
    "plugin2": "Pairing Success",
    "plugin3": "Confirm Ping",
    "plugin4": "Triple Ping",
    "charged1": "Achievement Unlocked",
    "charged2": "Victory Fanfare",
    "unplug": "Quick Shimmer",
}


def get_sounds_dir():
    for addon in addonHandler.getRunningAddons():
        if addon.manifest["name"] == "batteryChime":
            return os.path.join(addon.path, "sounds")
    return os.path.join(os.path.dirname(__file__), "..", "sounds")


def get_pack_sound_path(sound_id):
    return os.path.join(get_sounds_dir(), sound_id + ".wav")


def play_sound(path):
    def _play():
        try:
            nvwave.playWaveFile(path)
        except Exception:
            pass
    threading.Thread(target=_play, daemon=True).start()


def get_battery_percent():
    """Get current battery percentage using Windows API."""
    try:
        import ctypes
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]
        status = SYSTEM_POWER_STATUS()
        ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
        percent = status.BatteryLifePercent
        ac = status.ACLineStatus
        if percent == 255:
            return None, None  # No battery
        return percent, ac == 1  # percent, is_charging
    except Exception:
        return None, None


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(BatteryChimeSettingsPanel)
        self._alerted = set()
        self._wasCharging = None
        self._fullyChargedAlerted = False
        self._running = True
        self._thread = threading.Thread(target=self._monitorBattery, daemon=True)
        self._thread.start()

    def _monitorBattery(self):
        while self._running:
            try:
                percent, is_charging = get_battery_percent()
                if percent is not None:
                    if is_charging:
                        self._checkFullyCharged(percent)
                    else:
                        self._checkLevel(percent)
                        self._fullyChargedAlerted = False
                    self._checkChargingTransition(is_charging)
            except Exception:
                pass
            interval = config.conf["BatteryChime"]["checkInterval"]
            time.sleep(interval)

    def _checkLevel(self, percent):
        # Reset alerts when battery goes back up (e.g. plugged in)
        levels = [
            ("emergency", config.conf["BatteryChime"]["emergencyEnabled"], config.conf["BatteryChime"]["emergencyPercent"]),
            ("critical", config.conf["BatteryChime"]["criticalEnabled"], config.conf["BatteryChime"]["criticalPercent"]),
            ("low", config.conf["BatteryChime"]["lowEnabled"], config.conf["BatteryChime"]["lowPercent"]),
        ]

        triggered = False
        for level_name, enabled, threshold in levels:
            if enabled and percent <= threshold:
                if level_name not in self._alerted:
                    self._alerted.add(level_name)
                    self._playLevelSound(level_name)
                triggered = True
                break

        # Clear alerts for levels battery has risen above
        for level_name, enabled, threshold in levels:
            if percent > threshold + 2 and level_name in self._alerted:
                self._alerted.discard(level_name)

    def _checkChargingTransition(self, is_charging):
        # Skip the first observation -- it's the starting state, not a transition.
        if self._wasCharging is None:
            self._wasCharging = is_charging
            return
        if is_charging and not self._wasCharging:
            if config.conf["BatteryChime"]["pluggedInEnabled"]:
                self._playLevelSound("pluggedIn")
        elif not is_charging and self._wasCharging:
            if config.conf["BatteryChime"]["unpluggedEnabled"]:
                self._playLevelSound("unplugged")
        self._wasCharging = is_charging

    def _checkFullyCharged(self, percent):
        threshold = config.conf["BatteryChime"]["fullyChargedPercent"]
        if (
            config.conf["BatteryChime"]["fullyChargedEnabled"]
            and percent >= threshold
            and not self._fullyChargedAlerted
        ):
            self._fullyChargedAlerted = True
            self._playLevelSound("fullyCharged")

    def _playLevelSound(self, level):
        mode = config.conf["BatteryChime"][f"{level}Mode"]
        pack_key = config.conf["BatteryChime"][f"{level}PackSound"]
        custom_path = config.conf["BatteryChime"][f"{level}CustomPath"]

        if mode == "pack":
            path = get_pack_sound_path(pack_key)
            if os.path.isfile(path):
                play_sound(path)
        elif mode == "custom":
            if custom_path and os.path.isfile(custom_path):
                play_sound(custom_path)

    def terminate(self):
        self._running = False
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(BatteryChimeSettingsPanel)
        super().terminate()


class BatteryChimeSettingsPanel(gui.settingsDialogs.SettingsPanel):
    title = "BatteryChime"

    def makeSettings(self, sizer):
        helper = gui.guiHelper.BoxSizerHelper(self, sizer=sizer)
        packIds = list(SOUND_PACK.keys())
        modeMap = {"pack": 0, "custom": 1, "disabled": 2}

        # ── LOW BATTERY ──
        helper.addItem(wx.StaticText(self, label="Low Battery Warning"))

        self.lowEnabled = helper.addItem(wx.CheckBox(self, label="Enable low battery warning"))
        self.lowEnabled.SetValue(config.conf["BatteryChime"]["lowEnabled"])

        self.lowPercent = helper.addLabeledControl("Warn at percentage:", wx.SpinCtrl, min=5, max=95, initial=config.conf["BatteryChime"]["lowPercent"])

        self.lowMode = helper.addLabeledControl("Sound mode:", wx.Choice, choices=["Pack sound", "Custom WAV", "Disabled"])
        self.lowMode.SetSelection(modeMap.get(config.conf["BatteryChime"]["lowMode"], 0))

        self.lowPackSound = helper.addLabeledControl("Pack sound:", wx.Choice, choices=list(SOUND_PACK.values()))
        currentLow = config.conf["BatteryChime"]["lowPackSound"]
        self.lowPackSound.SetSelection(packIds.index(currentLow) if currentLow in packIds else 0)

        lowCustomSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.lowCustomPath = wx.TextCtrl(self, value=config.conf["BatteryChime"]["lowCustomPath"])
        lowCustomSizer.Add(self.lowCustomPath, proportion=1)
        self.lowBrowse = wx.Button(self, label="Browse...")
        self.lowBrowse.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.lowCustomPath))
        lowCustomSizer.Add(self.lowBrowse)
        helper.addItem(lowCustomSizer)

        self.lowPreview = helper.addItem(wx.Button(self, label="Test Low Warning"))
        self.lowPreview.Bind(wx.EVT_BUTTON, lambda e: self._testLevel("low"))

        # ── CRITICAL BATTERY ──
        helper.addItem(wx.StaticText(self, label="Critical Battery Warning"))

        self.criticalEnabled = helper.addItem(wx.CheckBox(self, label="Enable critical battery warning"))
        self.criticalEnabled.SetValue(config.conf["BatteryChime"]["criticalEnabled"])

        self.criticalPercent = helper.addLabeledControl("Warn at percentage:", wx.SpinCtrl, min=2, max=30, initial=config.conf["BatteryChime"]["criticalPercent"])

        self.criticalMode = helper.addLabeledControl("Sound mode:", wx.Choice, choices=["Pack sound", "Custom WAV", "Disabled"])
        self.criticalMode.SetSelection(modeMap.get(config.conf["BatteryChime"]["criticalMode"], 0))

        self.criticalPackSound = helper.addLabeledControl("Pack sound:", wx.Choice, choices=list(SOUND_PACK.values()))
        currentCritical = config.conf["BatteryChime"]["criticalPackSound"]
        self.criticalPackSound.SetSelection(packIds.index(currentCritical) if currentCritical in packIds else 0)

        criticalCustomSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.criticalCustomPath = wx.TextCtrl(self, value=config.conf["BatteryChime"]["criticalCustomPath"])
        criticalCustomSizer.Add(self.criticalCustomPath, proportion=1)
        self.criticalBrowse = wx.Button(self, label="Browse...")
        self.criticalBrowse.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.criticalCustomPath))
        criticalCustomSizer.Add(self.criticalBrowse)
        helper.addItem(criticalCustomSizer)

        self.criticalPreview = helper.addItem(wx.Button(self, label="Test Critical Warning"))
        self.criticalPreview.Bind(wx.EVT_BUTTON, lambda e: self._testLevel("critical"))

        # ── EMERGENCY BATTERY ──
        helper.addItem(wx.StaticText(self, label="Emergency Battery Warning"))

        self.emergencyEnabled = helper.addItem(wx.CheckBox(self, label="Enable emergency battery warning"))
        self.emergencyEnabled.SetValue(config.conf["BatteryChime"]["emergencyEnabled"])

        self.emergencyPercent = helper.addLabeledControl("Warn at percentage:", wx.SpinCtrl, min=1, max=15, initial=config.conf["BatteryChime"]["emergencyPercent"])

        self.emergencyMode = helper.addLabeledControl("Sound mode:", wx.Choice, choices=["Pack sound", "Custom WAV", "Disabled"])
        self.emergencyMode.SetSelection(modeMap.get(config.conf["BatteryChime"]["emergencyMode"], 0))

        self.emergencyPackSound = helper.addLabeledControl("Pack sound:", wx.Choice, choices=list(SOUND_PACK.values()))
        currentEmergency = config.conf["BatteryChime"]["emergencyPackSound"]
        self.emergencyPackSound.SetSelection(packIds.index(currentEmergency) if currentEmergency in packIds else 0)

        emergencyCustomSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.emergencyCustomPath = wx.TextCtrl(self, value=config.conf["BatteryChime"]["emergencyCustomPath"])
        emergencyCustomSizer.Add(self.emergencyCustomPath, proportion=1)
        self.emergencyBrowse = wx.Button(self, label="Browse...")
        self.emergencyBrowse.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.emergencyCustomPath))
        emergencyCustomSizer.Add(self.emergencyBrowse)
        helper.addItem(emergencyCustomSizer)

        self.emergencyPreview = helper.addItem(wx.Button(self, label="Test Emergency Warning"))
        self.emergencyPreview.Bind(wx.EVT_BUTTON, lambda e: self._testLevel("emergency"))

        # ── PLUGGED IN ──
        helper.addItem(wx.StaticText(self, label="Charger Plugged In"))

        self.pluggedInEnabled = helper.addItem(wx.CheckBox(self, label="Enable plugged in sound"))
        self.pluggedInEnabled.SetValue(config.conf["BatteryChime"]["pluggedInEnabled"])

        self.pluggedInMode = helper.addLabeledControl("Sound mode:", wx.Choice, choices=["Pack sound", "Custom WAV", "Disabled"])
        self.pluggedInMode.SetSelection(modeMap.get(config.conf["BatteryChime"]["pluggedInMode"], 0))

        self.pluggedInPackSound = helper.addLabeledControl("Pack sound:", wx.Choice, choices=list(SOUND_PACK.values()))
        currentPluggedIn = config.conf["BatteryChime"]["pluggedInPackSound"]
        self.pluggedInPackSound.SetSelection(packIds.index(currentPluggedIn) if currentPluggedIn in packIds else 0)

        pluggedInCustomSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.pluggedInCustomPath = wx.TextCtrl(self, value=config.conf["BatteryChime"]["pluggedInCustomPath"])
        pluggedInCustomSizer.Add(self.pluggedInCustomPath, proportion=1)
        self.pluggedInBrowse = wx.Button(self, label="Browse...")
        self.pluggedInBrowse.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.pluggedInCustomPath))
        pluggedInCustomSizer.Add(self.pluggedInBrowse)
        helper.addItem(pluggedInCustomSizer)

        self.pluggedInPreview = helper.addItem(wx.Button(self, label="Test Plugged In Sound"))
        self.pluggedInPreview.Bind(wx.EVT_BUTTON, lambda e: self._testLevel("pluggedIn"))

        # ── UNPLUGGED ──
        helper.addItem(wx.StaticText(self, label="Charger Unplugged"))

        self.unpluggedEnabled = helper.addItem(wx.CheckBox(self, label="Enable unplugged sound"))
        self.unpluggedEnabled.SetValue(config.conf["BatteryChime"]["unpluggedEnabled"])

        self.unpluggedMode = helper.addLabeledControl("Sound mode:", wx.Choice, choices=["Pack sound", "Custom WAV", "Disabled"])
        self.unpluggedMode.SetSelection(modeMap.get(config.conf["BatteryChime"]["unpluggedMode"], 0))

        self.unpluggedPackSound = helper.addLabeledControl("Pack sound:", wx.Choice, choices=list(SOUND_PACK.values()))
        currentUnplugged = config.conf["BatteryChime"]["unpluggedPackSound"]
        self.unpluggedPackSound.SetSelection(packIds.index(currentUnplugged) if currentUnplugged in packIds else 0)

        unpluggedCustomSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.unpluggedCustomPath = wx.TextCtrl(self, value=config.conf["BatteryChime"]["unpluggedCustomPath"])
        unpluggedCustomSizer.Add(self.unpluggedCustomPath, proportion=1)
        self.unpluggedBrowse = wx.Button(self, label="Browse...")
        self.unpluggedBrowse.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.unpluggedCustomPath))
        unpluggedCustomSizer.Add(self.unpluggedBrowse)
        helper.addItem(unpluggedCustomSizer)

        self.unpluggedPreview = helper.addItem(wx.Button(self, label="Test Unplugged Sound"))
        self.unpluggedPreview.Bind(wx.EVT_BUTTON, lambda e: self._testLevel("unplugged"))

        # ── FULLY CHARGED ──
        helper.addItem(wx.StaticText(self, label="Fully Charged"))

        self.fullyChargedEnabled = helper.addItem(wx.CheckBox(self, label="Enable fully charged sound"))
        self.fullyChargedEnabled.SetValue(config.conf["BatteryChime"]["fullyChargedEnabled"])

        self.fullyChargedPercent = helper.addLabeledControl("Consider fully charged at percentage:", wx.SpinCtrl, min=90, max=100, initial=config.conf["BatteryChime"]["fullyChargedPercent"])

        self.fullyChargedMode = helper.addLabeledControl("Sound mode:", wx.Choice, choices=["Pack sound", "Custom WAV", "Disabled"])
        self.fullyChargedMode.SetSelection(modeMap.get(config.conf["BatteryChime"]["fullyChargedMode"], 0))

        self.fullyChargedPackSound = helper.addLabeledControl("Pack sound:", wx.Choice, choices=list(SOUND_PACK.values()))
        currentFullyCharged = config.conf["BatteryChime"]["fullyChargedPackSound"]
        self.fullyChargedPackSound.SetSelection(packIds.index(currentFullyCharged) if currentFullyCharged in packIds else 0)

        fullyChargedCustomSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.fullyChargedCustomPath = wx.TextCtrl(self, value=config.conf["BatteryChime"]["fullyChargedCustomPath"])
        fullyChargedCustomSizer.Add(self.fullyChargedCustomPath, proportion=1)
        self.fullyChargedBrowse = wx.Button(self, label="Browse...")
        self.fullyChargedBrowse.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.fullyChargedCustomPath))
        fullyChargedCustomSizer.Add(self.fullyChargedBrowse)
        helper.addItem(fullyChargedCustomSizer)

        self.fullyChargedPreview = helper.addItem(wx.Button(self, label="Test Fully Charged Sound"))
        self.fullyChargedPreview.Bind(wx.EVT_BUTTON, lambda e: self._testLevel("fullyCharged"))

        # ── CHECK INTERVAL ──
        helper.addItem(wx.StaticText(self, label="General Settings"))
        self.checkInterval = helper.addLabeledControl(
            "Check battery every (seconds):",
            wx.SpinCtrl, min=10, max=300,
            initial=config.conf["BatteryChime"]["checkInterval"]
        )

    def _onBrowse(self, targetField):
        with wx.FileDialog(
            self, message="Choose a WAV file",
            wildcard="WAV files (*.wav)|*.wav",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                targetField.SetValue(dlg.GetPath())

    def _testLevel(self, level):
        modeCtrl = getattr(self, f"{level}Mode")
        packCtrl = getattr(self, f"{level}PackSound")
        pathCtrl = getattr(self, f"{level}CustomPath")
        packIds = list(SOUND_PACK.keys())
        sel = modeCtrl.GetSelection()
        if sel == 0:
            path = get_pack_sound_path(packIds[packCtrl.GetSelection()])
        elif sel == 1:
            path = pathCtrl.GetValue()
        else:
            return
        if path and os.path.isfile(path):
            play_sound(path)
        else:
            gui.messageBox(f"Sound file not found:\n{path}", "BatteryChime", wx.OK | wx.ICON_ERROR)

    def onSave(self):
        packIds = list(SOUND_PACK.keys())
        modeMap = {0: "pack", 1: "custom", 2: "disabled"}

        config.conf["BatteryChime"]["lowEnabled"] = self.lowEnabled.GetValue()
        config.conf["BatteryChime"]["lowPercent"] = self.lowPercent.GetValue()
        config.conf["BatteryChime"]["lowMode"] = modeMap[self.lowMode.GetSelection()]
        config.conf["BatteryChime"]["lowPackSound"] = packIds[self.lowPackSound.GetSelection()]
        config.conf["BatteryChime"]["lowCustomPath"] = self.lowCustomPath.GetValue()

        config.conf["BatteryChime"]["criticalEnabled"] = self.criticalEnabled.GetValue()
        config.conf["BatteryChime"]["criticalPercent"] = self.criticalPercent.GetValue()
        config.conf["BatteryChime"]["criticalMode"] = modeMap[self.criticalMode.GetSelection()]
        config.conf["BatteryChime"]["criticalPackSound"] = packIds[self.criticalPackSound.GetSelection()]
        config.conf["BatteryChime"]["criticalCustomPath"] = self.criticalCustomPath.GetValue()

        config.conf["BatteryChime"]["emergencyEnabled"] = self.emergencyEnabled.GetValue()
        config.conf["BatteryChime"]["emergencyPercent"] = self.emergencyPercent.GetValue()
        config.conf["BatteryChime"]["emergencyMode"] = modeMap[self.emergencyMode.GetSelection()]
        config.conf["BatteryChime"]["emergencyPackSound"] = packIds[self.emergencyPackSound.GetSelection()]
        config.conf["BatteryChime"]["emergencyCustomPath"] = self.emergencyCustomPath.GetValue()

        config.conf["BatteryChime"]["pluggedInEnabled"] = self.pluggedInEnabled.GetValue()
        config.conf["BatteryChime"]["pluggedInMode"] = modeMap[self.pluggedInMode.GetSelection()]
        config.conf["BatteryChime"]["pluggedInPackSound"] = packIds[self.pluggedInPackSound.GetSelection()]
        config.conf["BatteryChime"]["pluggedInCustomPath"] = self.pluggedInCustomPath.GetValue()

        config.conf["BatteryChime"]["unpluggedEnabled"] = self.unpluggedEnabled.GetValue()
        config.conf["BatteryChime"]["unpluggedMode"] = modeMap[self.unpluggedMode.GetSelection()]
        config.conf["BatteryChime"]["unpluggedPackSound"] = packIds[self.unpluggedPackSound.GetSelection()]
        config.conf["BatteryChime"]["unpluggedCustomPath"] = self.unpluggedCustomPath.GetValue()

        config.conf["BatteryChime"]["fullyChargedEnabled"] = self.fullyChargedEnabled.GetValue()
        config.conf["BatteryChime"]["fullyChargedPercent"] = self.fullyChargedPercent.GetValue()
        config.conf["BatteryChime"]["fullyChargedMode"] = modeMap[self.fullyChargedMode.GetSelection()]
        config.conf["BatteryChime"]["fullyChargedPackSound"] = packIds[self.fullyChargedPackSound.GetSelection()]
        config.conf["BatteryChime"]["fullyChargedCustomPath"] = self.fullyChargedCustomPath.GetValue()

        config.conf["BatteryChime"]["checkInterval"] = self.checkInterval.GetValue()
