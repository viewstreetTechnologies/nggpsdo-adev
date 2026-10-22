"""Dependency-free Tk desktop interface."""
import csv
from decimal import Decimal
import math
import queue
import time
import tkinter as tk
from tkinter import font as tkfont
import ipaddress
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .analysis import Analysis, Sample
from .stream import Client, parameter_url
from .graph_image import save_svg
from .host_history import history_path, load_hosts, save_hosts

def axis_number(value, precision=6):
    """Keep significant digits but render axis labels without exponents."""
    number = Decimal(format(value, f".{precision}g"))
    if number == 0:
        return "0"
    label = format(number, "f")
    return label.rstrip("0").rstrip(".") if "." in label else label


FIELDS = ["sampleNumber", "endTimeUnixMs", "intervalUs", "frequencyHz",
          "nominalHz", "flags", "eventId", "ocxoTemperatureC", "controlVoltageV",
          "controllerFrequencyHz", "controllerFilterLevel"]

TIME_VIEWS = {
    "Frequency": ("Frequency offset (Hz)", "controller_offset_hz",
        lambda s: None if s.controller_frequency is None else s.controller_frequency - s.nominal,
        "#83b7ff"),
    "Raw": ("Raw offset (Hz)", "offset_hz",
                         lambda s: s.frequency - s.nominal, "#4ed9cc"),
    "OCXO temperature": ("OCXO temperature (°C)", "ocxoTemperatureC",
                         lambda s: s.temperature_c, "#ffb454"),
    "Control voltage": ("Control voltage (V)", "controlVoltageV",
                        lambda s: s.control_voltage_v, "#b99aff"),
}

TIME_RANGES = {"5 minutes": 300000, "30 minutes": 1800000,
               "1 hour": 3600000, "1 day": 86400000,
               "1 week": 604800000, "All data": None}


def format_duration(milliseconds):
    sign = "−" if milliseconds < 0 else "+"
    days, remainder = divmod(abs(int(milliseconds)), 86400000)
    hours, remainder = divmod(remainder, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{remainder / 1000:.3f}s")
    return sign + " ".join(parts)


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"NGGPSDO ADEV Monitor v{__version__}")
        menu = tk.Menu(root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Start CSV recording…", command=self.record_csv)
        file_menu.add_command(label="Stop recording", command=self.stop_record)
        file_menu.add_separator()
        file_menu.add_command(label="Open CSV…", command=self.open_csv)
        file_menu.add_command(label="Export graph CSV…", command=self.export)
        file_menu.add_command(label="Save graph image…", command=self.save_image)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menu.add_cascade(label="File", menu=file_menu)
        root.configure(menu=menu)
        self.analysis = Analysis()
        self.client = None
        self.record = None
        self.writer = None
        self.record_started = None
        self.record_path = ""
        self.record_count = 0
        self.record_status = tk.StringVar(value="Not recording")
        self.drag_state = None
        self.last_plot = 0
        self.dirty = True
        self.hover_points = {}
        self.hover_key = None
        self.hover_match = None
        self.pinned_points = {}
        self.pinned_matches = {}
        self.pin_keys = {}
        self.label_positions = {}
        self.label_drag = None
        self.pan_moved = False
        self.zoom_limits = {}
        self.plot_bounds = None
        self.output = queue.Queue(maxsize=4096)
        self.history_path = history_path()
        self.host_history = load_hosts(self.history_path)
        recent = self.host_history[0] if self.host_history else {"host": "nggpsdo", "port": 80}
        self.hostname = tk.StringVar(value=recent["host"])
        self.port = tk.StringVar(value=str(recent["port"]))
        self.event_type = tk.StringVar(value="fc")
        self.after_id = tk.StringVar(value="Live only")
        self.replay_interval = tk.StringVar(value="1000")
        self.status = tk.StringVar(value="Disconnected — connect to your NGGPSDO")
        self.stats = tk.StringVar(value="Waiting for frequency measurements")
        self.ppm = tk.StringVar(value="100")
        self.view = tk.StringVar(value="ADEV")
        self.time_mode = tk.StringVar(value="Local")
        self.temperature_unit = tk.StringVar(value="°C")
        self.follow_live = tk.BooleanVar(value=True)
        self.time_range = tk.StringVar(value="All data")
        self.show_frequency = tk.BooleanVar(value=False)
        self.show_temperature = tk.BooleanVar(value=True)
        self.show_voltage = tk.BooleanVar(value=True)
        self.show_controller = tk.BooleanVar(value=True)
        self.traces = {"Frequency": self.show_controller,
                       "Raw": self.show_frequency,
                       "Control voltage": self.show_voltage,
                       "OCXO temperature": self.show_temperature}
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"NGGPSDO ADEV Monitor v{__version__}", font=("Segoe UI", 21, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Frequency stability and combined frequency / voltage / temperature time plot").pack(anchor="w", pady=(0, 12))
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        ttk.Label(bar, text="Hostname / IP: ").pack(side="left")
        self.host_selector = ttk.Combobox(bar, textvariable=self.hostname, width=28,
            values=[entry["host"] for entry in self.host_history])
        self.host_selector.pack(side="left")
        self.host_selector.bind("<<ComboboxSelected>>", self.select_host)
        ttk.Label(bar, text="  Port: ").pack(side="left")
        ttk.Entry(bar, textvariable=self.port, width=6).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Connect", command=self.connect).pack(side="left", padx=4)
        ttk.Button(bar, text="Disconnect", command=self.disconnect).pack(side="left")
        options = ttk.Frame(frame)
        options.pack(fill="x", pady=8)
        for label, variable, values, width in (
                ("Event type", self.event_type, ("fc", "1"), 8),
                ("After event ID", self.after_id, ("Live only", "0"), 19),
                ("Replay interval (ms)", self.replay_interval,
                 ("1", "100", "1000", "4000", "8000"), 9)):
            ttk.Label(options, text=label + ": ").pack(side="left")
            ttk.Combobox(options, textvariable=variable, values=values, width=width).pack(side="left", padx=(0, 8))
        filtering = ttk.Frame(frame)
        filtering.pack(fill="x", pady=4)
        ttk.Label(filtering, text="Reject |offset| above (ppm): ").pack(side="left")
        ttk.Entry(filtering, textvariable=self.ppm, width=8).pack(side="left")
        ttk.Label(frame, textvariable=self.status, wraplength=950).pack(anchor="w")
        ttk.Label(frame, textvariable=self.stats).pack(anchor="w", pady=8)
        views = ttk.Frame(frame)
        views.pack(fill="x", pady=(0, 8))
        ttk.Label(views, text="Graph: ").pack(side="left")
        for name in ("ADEV", "MDEV", "HDEV", "Time plot"):
            ttk.Radiobutton(views, text=name, variable=self.view, value=name,
                            command=self.draw).pack(side="left", padx=8)
        self.time_controls = []
        traces_bar = ttk.Frame(frame)
        traces_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(traces_bar, text="Show: ").pack(side="left")
        for name, variable in self.traces.items():
            checkbox = ttk.Checkbutton(traces_bar, text=name, variable=variable, command=self.draw)
            checkbox.pack(side="left", padx=8)
            self.time_controls.append(checkbox)
        follow = ttk.Checkbutton(traces_bar, text="Follow live", variable=self.follow_live,
                                 command=self.toggle_follow)
        follow.pack(side="left", padx=12)
        self.time_controls.append(follow)
        display_bar = ttk.Frame(frame)
        display_bar.pack(fill="x", pady=(0, 8))
        for label, variable, choices in (("Time", self.time_mode, ("Local", "UTC")),
                                         ("Temperature", self.temperature_unit, ("°C", "°F"))):
            ttk.Label(display_bar, text=label + ": ").pack(side="left")
            selector = ttk.Combobox(display_bar, textvariable=variable, values=choices,
                                    state="readonly", width=8)
            selector.pack(side="left", padx=(0, 14))
            selector.bind("<<ComboboxSelected>>", lambda event: self.draw())
            self.time_controls.append(selector)
        ttk.Label(display_bar, text="Range: ").pack(side="left")
        range_selector = ttk.Combobox(display_bar, textvariable=self.time_range,
            values=(*TIME_RANGES, "Custom"), state="readonly", width=11)
        range_selector.pack(side="left")
        range_selector.bind("<<ComboboxSelected>>", self.select_time_range)
        self.time_controls.append(range_selector)
        self.canvas = tk.Canvas(frame, background="#101b2b", highlightthickness=0, height=330)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<Leave>", self.hide_tooltip)
        self.canvas.bind("<MouseWheel>", self.zoom_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.zoom_wheel(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self.zoom_wheel(e, -1))
        self.canvas.bind("<Double-Button-1>", self.reset_zoom)
        self.canvas.bind("<ButtonPress-1>", self.start_pan)
        self.canvas.bind("<B1-Motion>", self.pan)
        self.canvas.bind("<ButtonRelease-1>", self.end_pan)
        self.canvas.bind("<Button-3>", self.dismiss_points)
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill="x", pady=8)
        self.table = ttk.Treeview(table_frame,
            columns=("utc", *self.traces, "run"),
            show="headings", height=5)
        self.table.heading("utc", text="UTC measurement end")
        self.table.heading("run", text="Continuous run")
        self.table.column("utc", width=190)
        self.table.column("run", width=100)
        for name in self.traces:
            self.table.heading(name, text=TIME_VIEWS[name][0])
            self.table.column(name, width=170)
        self.table.pack(side="left", fill="x", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scrollbar.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scrollbar.set)
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        for label, command in [("Reset zoom", self.reset_zoom), ("Clear", self.clear)]:
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 6))
        ttk.Label(frame, textvariable=self.record_status, wraplength=950).pack(anchor="w", pady=(6, 0))
        ttk.Label(frame, text="200,000-sample rolling window • independent trace scales • missing readings break the line").pack(anchor="w", pady=(10, 0))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.fit_window()
        root.after(100, self.poll)

    def fit_window(self):
        self.root.update_idletasks()
        left, top = 0, 0
        right, bottom = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        try:
            import ctypes
            from ctypes import wintypes
            area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0):
                left, top, right, bottom = area.left, area.top, area.right, area.bottom
        except (AttributeError, OSError):
            pass
        # Leave room for native title/menu borders and the desktop taskbar.
        available_w, available_h = max(1, right-left-24), max(1, bottom-top-72)
        required_w, required_h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        width = min(available_w, max(1050, required_w+20))
        height = min(available_h, max(740, required_h+20))
        self.root.minsize(min(width, required_w), min(height, max(600, required_h-150)))
        self.root.geometry(f"{width}x{height}+{left+(right-left-width)//2}+{top+12}")

    def disconnect(self):
        if self.client:
            self.client.stop()
            self.client = None
        # Separate queues prevent a stopped connection delivering into a new session.
        self.output = queue.Queue(maxsize=4096)
        self.status.set("Disconnected")

    def connect(self):
        try:
            url = self.connection_url()
            ppm = float(self.ppm.get())
            if not math.isfinite(ppm) or ppm <= 0:
                raise ValueError("Outlier threshold must be positive")
        except ValueError as error:
            messagebox.showerror("Connection settings", str(error))
            return
        self.disconnect()
        self.clear()
        self.analysis.max_ppm = ppm
        self.remember_host()
        self.client = Client(url, self.output)
        self.client.start()

    def select_host(self, event=None):
        for entry in self.host_history:
            if entry["host"].casefold() == self.hostname.get().casefold():
                self.port.set(str(entry["port"]))
                break

    def remember_host(self):
        host = self.hostname.get().strip()
        self.host_history = [{"host": host, "port": int(self.port.get())}] + [
            entry for entry in self.host_history if entry["host"].casefold() != host.casefold()]
        self.host_history = self.host_history[:10]
        self.host_selector.configure(values=[entry["host"] for entry in self.host_history])
        save_hosts(self.history_path, self.host_history)

    def connection_url(self):
        host = self.hostname.get().strip()
        port = self.port.get().strip()
        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError("Port must be an integer from 1 to 65535")
        if not host or any(c.isspace() or c in "/?#@" for c in host):
            raise ValueError("Enter a hostname or IP address without a URL or path")
        if ":" in host or "[" in host or "]" in host:
            try:
                host = "[" + str(ipaddress.IPv6Address(host.removeprefix("[").removesuffix("]"))) + "]"
            except ValueError:
                raise ValueError("Enter the hostname and port in their separate fields") from None
        else:
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError:
                raise ValueError("Invalid hostname") from None
            labels = host.rstrip(".").split(".")
            if len(host) > 253 or any(not 1 <= len(label) <= 63
                    or label.startswith("-") or label.endswith("-")
                    or any(not (c.isalnum() or c == "-") for c in label) for label in labels):
                raise ValueError("Invalid hostname or IP address")
        authority = host if int(port) == 80 else f"{host}:{int(port)}"
        return parameter_url(f"http://{authority}/events", self.event_type.get(),
                             self.after_id.get(), self.replay_interval.get())

    def accept(self, sample):
        valid = self.analysis.add(sample)
        if self.writer:
            try:
                self.writer.writerow([sample.sequence, sample.timestamp, sample.interval,
                                      sample.frequency, sample.nominal, sample.flags, sample.event_id,
                                      sample.temperature_c, sample.control_voltage_v,
                                      sample.controller_frequency, sample.controller_level])
                self.record.flush()
                self.record_count += 1
            except OSError as error:
                self.stop_record()
                messagebox.showerror("Recording stopped", str(error))
        self.stats.set(f"Accepted: {self.analysis.accepted:,}    Rejected: {self.analysis.rejected:,}    "
                       f"Run breaks: {self.analysis.breaks:,}    Frequency: {sample.frequency:.9f} Hz"
                       + ("" if valid else "  [rejected]"))
        self.dirty = True

    def poll(self):
        self.update_record_status()
        for _ in range(500):
            try:
                kind, value = self.output.get_nowait()
            except queue.Empty:
                break
            if kind == "sample":
                self.accept(value)
            else:
                self.status.set(value)
        if self.dirty and time.monotonic() - self.last_plot > 2:
            self.draw()
            self.last_plot = time.monotonic()
            self.dirty = False
        self.root.after(100, self.poll)

    def selected_traces(self):
        return [name for name, variable in self.traces.items() if variable.get()]

    def display_trace(self, name):
        label, column, value_of, color = TIME_VIEWS[name]
        if name == "OCXO temperature" and self.temperature_unit.get() == "°F":
            return ("OCXO temperature (°F)", column,
                    lambda s: None if s.temperature_c is None else s.temperature_c*9/5+32,
                    color)
        return label, column, value_of, color

    def format_time(self, timestamp, full=False):
        convert = time.gmtime if self.time_mode.get() == "UTC" else time.localtime
        return time.strftime("%Y-%m-%d %H:%M:%S" if full else "%H:%M:%S",
                             convert(timestamp/1000))

    def draw(self):
        self.hover_match = None
        self.pinned_matches = {}
        self.pin_keys = {}
        self.plot_bounds = None
        self.draw_plot()
        for number, key in enumerate(self.pinned_points):
            if key in self.pinned_matches:
                tag = f"pin_{number}"
                self.pin_keys[tag] = key
                self.show_tooltip(*self.pinned_matches[key], tag=tag)
        if self.hover_match is not None:
            self.show_tooltip(*self.hover_match)
        else:
            self.hide_tooltip()

    def draw_plot(self):
        self.hover_points.clear()
        is_time = self.view.get() == "Time plot"
        for control in self.time_controls:
            control.state(["!disabled" if is_time else "disabled"])
        if not is_time:
            self.draw_stability(self.view.get())
            return
        self.table.configure(columns=("utc", *self.traces, "run"))
        self.table.heading("run", text="Continuous run")
        selected = self.selected_traces()
        samples = list(self.analysis.samples)
        time_label = "UTC" if self.time_mode.get() == "UTC" else "Local " + time.strftime("%Z")
        self.table.heading("utc", text=f"{self.time_mode.get()} measurement end")
        for name in self.traces:
            self.table.heading(name, text=self.display_trace(name)[0])
        self.table.configure(displaycolumns=("utc", *selected, "run"))
        self.table.delete(*self.table.get_children())
        for run, sample in samples[-100:]:
            stamp = self.format_time(sample.timestamp, full=True)
            values = [self.display_trace(name)[2](sample) for name in self.traces]
            self.table.insert("", "end", values=(stamp, *(
                "Unavailable" if value is None else f"{value:.9g}" for value in values), run))
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 600), max(c.winfo_height(), 180)
        left, right = 100, w-30-105*max(0, len(selected)-1)
        top, bottom = 40, h-55
        c.create_text((left+right)/2, h-15, text=f"Measurement time ({time_label})", fill="#dbe7f5")
        if not selected:
            c.create_text(w/2, h/2, text="Select a trace to show", fill="#dbe7f5")
            return
        if not samples:
            c.create_text(w/2, h/2, text="Waiting for valid measurements", fill="#dbe7f5")
            return
        xmin = min(s.timestamp for _, s in samples)
        xmax = max(xmin+4000, max(s.timestamp for _, s in samples))
        full_limits = (xmin, xmax)
        preset = TIME_RANGES.get(self.time_range.get())
        if self.follow_live.get() and preset is not None:
            self.zoom_limits["Time plot"] = (max(xmin, xmax-preset), xmax)
        elif self.follow_live.get() and "Time plot" in self.zoom_limits:
            old_low, old_high = self.zoom_limits["Time plot"]
            self.zoom_limits["Time plot"] = (max(xmin, xmax-(old_high-old_low)), xmax)
        xmin, xmax = self.zoom_limits.get("Time plot", full_limits)
        self.plot_bounds = (left, right, top, bottom, xmin, xmax, full_limits)
        samples = [(run, s) for run, s in samples if xmin <= s.timestamp <= xmax]
        for tick in range(5):
            ratio = tick/4
            x = left+ratio*(right-left)
            y = bottom-ratio*(bottom-top)
            c.create_line(x, top, x, bottom, fill="#30435b")
            c.create_line(left, y, right, y, fill="#30435b")
            stamp = self.format_time(xmin+ratio*(xmax-xmin))
            c.create_text(x, bottom+16, text=stamp, fill="#dbe7f5")
        for index, name in enumerate(selected):
            label, _, value_of, color = self.display_trace(name)
            axis_x = left if index == 0 else right+(index-1)*105
            direction = -1 if index == 0 else 1
            title = {"Raw": "Raw\noffset (Hz)", "Frequency": "Frequency\noffset (Hz)", "Control voltage": "Voltage (V)",
                     "OCXO temperature": f"Temp ({self.temperature_unit.get()})"}[name]
            c.create_text(axis_x + direction*40, 16, text=title, fill=color)
            c.create_line(axis_x, top, axis_x, bottom, fill=color)
            available = [value_of(s) for _, s in samples if value_of(s) is not None]
            if not available:
                c.create_text(axis_x+direction*45, top+15, text="Unavailable", fill=color)
                continue
            low, high = min(available), max(available)
            padding = max((high-low)*.1, 1e-6)
            low, high = low-padding, high+padding
            for tick in range(5):
                ratio = tick/4
                y = bottom-ratio*(bottom-top)
                c.create_line(axis_x, y, axis_x+direction*5, y, fill=color)
                c.create_text(axis_x+direction*8, y, text=axis_number(low+ratio*(high-low)),
                              anchor="e" if index == 0 else "w", fill=color)
            previous = None
            tag = {"Raw": "trace_frequency", "Frequency": "trace_controller", "Control voltage": "trace_voltage",
                   "OCXO temperature": "trace_temperature"}[name]
            for run, sample in samples:
                value = value_of(sample)
                if value is None:
                    previous = None
                    continue
                x = left+(sample.timestamp-xmin)/(xmax-xmin)*(right-left)
                y = bottom-(value-low)/(high-low)*(bottom-top)
                self.index_point(x, y, ("time", name, run, sample))
                if previous and previous[0] == run:
                    c.create_line(previous[1], previous[2], x, y, fill=color, width=2, tags=tag)
                if len(samples) < 1000:
                    c.create_oval(x-2, y-2, x+2, y+2, fill=color, outline="", tags=tag)
                previous = run, x, y
    def draw_stability(self, kind):
        points = self.analysis.curve(kind)
        self.table.configure(displaycolumns="#all", columns=("tau", "deviation", "terms"))
        for col, label in (("tau", "τ (seconds)"), ("deviation", f"{kind} (fractional frequency)"),
                           ("terms", "Overlapping terms")):
            self.table.heading(col, text=label)
        self.table.delete(*self.table.get_children())
        for tau, deviation, terms in points:
            self.table.insert("", "end", values=(f"{tau:g}", f"{deviation:.5e}", terms))
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 600), max(c.winfo_height(), 180)
        left, right, top, bottom = 85, w-30, 30, h-55
        c.create_text(w/2, h-15, text="Averaging time τ (seconds, logarithmic)", fill="#dbe7f5")
        c.create_text(18, h/2, text=f"{kind} (fractional frequency)", angle=90, fill="#dbe7f5")
        positive = [(tau, value) for tau, value, _ in points if value > 0]
        if not positive:
            c.create_text(w/2, h/2,
                text="Waiting for sufficient adjacent valid samples\nZero deviation values appear in the table",
                fill="#dbe7f5", justify="center")
            return
        xmin = math.floor(math.log10(min(t for t, v in positive)))
        xmax = max(xmin+1, math.ceil(math.log10(max(t for t, v in positive))))
        full_limits = (xmin, xmax)
        xmin, xmax = self.zoom_limits.get(kind, full_limits)
        positive = [(t, v) for t, v in positive if xmin <= math.log10(t) <= xmax]
        if not positive:
            c.create_text(w/2, h/2, text="No stability points in this zoom range", fill="#dbe7f5")
            return
        ymin = math.floor(math.log10(min(v for t, v in positive)))
        ymax = max(ymin+1, math.ceil(math.log10(max(v for t, v in positive))))
        font = tkfont.nametofont("TkDefaultFont")
        left = max(left, max(font.measure(axis_number(10**power))
                             for power in range(ymin, ymax+1)) + 50)
        self.plot_bounds = (left, right, top, bottom, xmin, xmax, full_limits)
        def xy(t, v):
            return (left+(math.log10(t)-xmin)/(xmax-xmin)*(right-left),
                    bottom-(math.log10(v)-ymin)/(ymax-ymin)*(bottom-top))
        for tick in range(5):
            power = xmin + tick/4*(xmax-xmin)
            x, _ = xy(10**power, 10**ymin)
            c.create_line(x, top, x, bottom, fill="#30435b")
            c.create_text(x, bottom+16, text=axis_number(10**power, 3), fill="#dbe7f5")
        for power in range(ymin, ymax+1):
            _, y = xy(10**xmin, 10**power)
            c.create_line(left, y, right, y, fill="#30435b")
            c.create_text(left-8, y, text=axis_number(10**power), anchor="e", fill="#dbe7f5")
        previous = None
        term_counts = {tau: terms for tau, _, terms in points}
        for tau, value in positive:
            x, y = xy(tau, value)
            self.index_point(x, y, ("stability", kind, tau, value, term_counts[tau]))
            if previous:
                c.create_line(*previous, x, y, fill="#4ed9cc", width=2)
            c.create_oval(x-4, y-4, x+4, y+4, fill="#4ed9cc", outline="")
            previous = (x, y)

    def zoom_wheel(self, event, direction=None):
        if self.plot_bounds is None:
            return "break"
        left, right, top, bottom, low, high, full = self.plot_bounds
        if not (left <= event.x <= right and top <= event.y <= bottom):
            return "break"
        direction = direction if direction is not None else getattr(event, "delta", 0)
        if not direction:
            return "break"
        if self.view.get() == "Time plot":
            self.follow_live.set(False)
            self.time_range.set("Custom")
        ratio = (event.x-left)/(right-left)
        anchor = low+ratio*(high-low)
        minimum = 4000 if self.view.get() == "Time plot" else .05
        span = min(full[1]-full[0], max(minimum, (high-low)*(.8 if direction > 0 else 1.25)))
        start = max(full[0], min(anchor-ratio*span, full[1]-span))
        if span >= full[1]-full[0]:
            self.zoom_limits.pop(self.view.get(), None)
        else:
            self.zoom_limits[self.view.get()] = (start, start+span)
        self.draw()
        return "break"

    def reset_zoom(self, event=None):
        self.end_pan()
        self.zoom_limits.pop(self.view.get(), None)
        if self.view.get() == "Time plot":
            self.follow_live.set(True)
            self.time_range.set("All data")
        self.draw()

    def select_time_range(self, event=None):
        if self.time_range.get() == "Custom":
            return
        self.end_pan()
        self.zoom_limits.pop("Time plot", None)
        self.follow_live.set(True)
        self.draw()

    def toggle_follow(self):
        if not self.follow_live.get() and self.plot_bounds and self.view.get() == "Time plot":
            self.zoom_limits["Time plot"] = self.plot_bounds[4:6]
        self.draw()

    def start_pan(self, event):
        self.pan_moved = False
        for item in reversed(self.canvas.find_overlapping(event.x, event.y, event.x, event.y)):
            tags = self.canvas.gettags(item)
            if "pinned_box" in tags:
                key = next((self.pin_keys[tag] for tag in tags if tag in self.pin_keys), None)
                if key is not None:
                    x1, y1, x2, y2 = self.canvas.coords(item)
                    self.label_drag = (key, event.x, event.y, x1+6, y1+5, x2-x1-12, y2-y1-10)
                    self.drag_state = None
                    self.hide_tooltip()
                    self.canvas.configure(cursor="fleur")
                    return
        if self.plot_bounds:
            left, right, top, bottom, low, high, full = self.plot_bounds
            if left <= event.x <= right and top <= event.y <= bottom:
                self.drag_state = (self.view.get(), event.x, low, high, left, right, full)

    def pan(self, event):
        if self.label_drag:
            key, origin_x, origin_y, left, top, width, height = self.label_drag
            x = max(10, min(left+event.x-origin_x, self.canvas.winfo_width()-width-10))
            y = max(10, min(top+event.y-origin_y, self.canvas.winfo_height()-height-10))
            self.label_positions[key] = (x, y)
            self.draw()
            return
        if not self.drag_state:
            return
        kind, origin, low, high, left, right, full = self.drag_state
        if kind != self.view.get() or abs(event.x-origin) < 3:
            return
        self.pan_moved = True
        if kind == "Time plot":
            self.follow_live.set(False)
            self.time_range.set("Custom")
        self.hide_tooltip()
        self.canvas.configure(cursor="fleur")
        span = high-low
        shift = (origin-event.x)/(right-left)*span
        start = max(full[0], min(low+shift, full[1]-span))
        self.zoom_limits[kind] = (start, start+span)
        self.draw()

    def end_pan(self, event=None):
        if self.label_drag:
            self.label_drag = None
            self.canvas.configure(cursor="")
            return
        clicked = event is not None and self.drag_state is not None and not self.pan_moved
        self.drag_state = None
        self.canvas.configure(cursor="")
        if clicked:
            point = self.nearest_point(event)
            if point:
                self.pinned_points[self.point_key(point[2])] = point[2]
                self.hide_tooltip()
                self.draw()

    def dismiss_points(self, event=None):
        if event is None:
            self.pinned_points.clear()
            self.label_positions.clear()
        else:
            # Hit-test the label rectangle, not its point or leader line.
            for item in reversed(self.canvas.find_overlapping(event.x, event.y, event.x, event.y)):
                tags = self.canvas.gettags(item)
                if self.canvas.type(item) != "rectangle":
                    continue
                if "tooltip" in tags:
                    self.hide_tooltip()
                    return "break"
                if "pinned_box" in tags:
                    key = next((self.pin_keys[tag] for tag in tags if tag in self.pin_keys), None)
                    if key is not None:
                        self.pinned_points.pop(key, None)
                        self.label_positions.pop(key, None)
                        break
            else:
                return "break"
        self.hide_tooltip()
        self.draw()
        return "break"

    def index_point(self, x, y, info):
        # Index actual samples even when dense plots omit visible point markers.
        self.hover_points.setdefault((int(x//16), int(y//16)), []).append((x, y, info))
        if self.point_key(info) == self.hover_key:
            self.hover_match = (x, y, info)
        if self.point_key(info) in self.pinned_points:
            self.pinned_matches[self.point_key(info)] = (x, y, info)
            self.pinned_points[self.point_key(info)] = info

    @staticmethod
    def point_key(info):
        if info[0] == "time":
            return ("time", info[1], id(info[3]))
        return ("stability", info[1], info[2])

    def hide_tooltip(self, event=None):
        self.hover_key = None
        self.hover_match = None
        self.canvas.delete("tooltip")

    def hover(self, event):
        if self.drag_state or self.label_drag:
            return
        c = self.canvas
        c.delete("tooltip")
        nearest = self.nearest_point(event)
        if nearest is None or self.point_key(nearest[2]) in self.pinned_points:
            self.hide_tooltip()
            return
        self.hover_key = self.point_key(nearest[2])
        self.show_tooltip(*nearest)

    def nearest_point(self, event):
        bx, by = int(event.x//16), int(event.y//16)
        nearest, distance = None, 81
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for x, y, info in self.hover_points.get((bx+dx, by+dy), ()):
                    squared = (x-event.x)**2 + (y-event.y)**2
                    if squared < distance:
                        nearest, distance = (x, y, info), squared
        return nearest

    def show_tooltip(self, x, y, info, tag="tooltip"):
        c = self.canvas
        c.delete(tag)
        if info[0] == "time":
            _, name, run, sample = info[:4]
            stamp = self.format_time(sample.timestamp, full=True)
            zone = "UTC" if self.time_mode.get() == "UTC" else time.strftime("%Z", time.localtime(sample.timestamp/1000))
            lines = [name, f"{stamp}.{sample.timestamp % 1000:03d} {zone}"]
            if sample.controller_frequency is not None:
                lines.extend([f"Frequency: {sample.controller_frequency:.12g} Hz",
                              f"Filter: L{sample.controller_level}"])
            lines.append(f"Raw: {sample.frequency:.12g} Hz")
            for trace in self.selected_traces():
                label, _, value_of, _ = self.display_trace(trace)
                value = value_of(sample)
                lines.append(f"{label}: " + ("Unavailable" if value is None else f"{value:.10g}"))
            lines.append(f"Sample: {sample.sequence}   Run: {run}")
            if sample.event_id:
                lines.append(f"Event ID: {sample.event_id}")
        else:
            _, kind, tau, value, terms = info
            lines = [kind, f"Averaging time τ: {tau:g} s",
                     f"Deviation: {value:.8e}", f"Overlapping terms: {terms:,}"]
        if tag != "tooltip":
            lines.extend(self.pin_deltas(info))
        c.create_oval(x-5, y-5, x+5, y+5, outline="#ffffff", tags=tag)
        text = c.create_text(x+14, y+14, text="\n".join(lines),
                             anchor="nw", justify="left", fill="#f3f6fb", tags=tag)
        bounds = c.bbox(text)
        shift_x = min(0, c.winfo_width()-bounds[2]-10)
        shift_y = min(0, c.winfo_height()-bounds[3]-10)
        c.move(text, shift_x, shift_y)
        bounds = c.bbox(text)
        c.move(text, max(0, 10-bounds[0]), max(0, 10-bounds[1]))
        if tag != "tooltip":
            # Place additional pinned labels without covering previous labels
            # when space permits; keep a leader line back to each data point.
            bx1, by1, bx2, by2 = c.bbox(text)
            width, height = bx2-bx1, by2-by1
            boxes = [c.bbox(item) for item in c.find_withtag("pinned_box")]
            candidates = [(bx1, by1)] + [(xx, yy)
                for yy in range(10, max(11, c.winfo_height()-height-8), height+14)
                for xx in range(10, max(11, c.winfo_width()-width-8), width+14)]
            def overlap(position):
                px, py = position
                return sum(max(0, min(px+width+6, b[2])-max(px-6, b[0])) *
                           max(0, min(py+height+5, b[3])-max(py-5, b[1])) for b in boxes)
            saved_position = self.label_positions.get(self.point_key(info))
            if saved_position:
                px = max(10, min(saved_position[0], c.winfo_width()-width-10))
                py = max(10, min(saved_position[1], c.winfo_height()-height-10))
            else:
                px, py = min(candidates, key=lambda p: (overlap(p), (p[0]-bx1)**2+(p[1]-by1)**2))
            c.move(text, px-bx1, py-by1)
            c.create_line(x, y, px, py, fill="#8195af", dash=(3, 3), tags=tag)
        x1, y1, x2, y2 = c.bbox(text)
        box = c.create_rectangle(x1-6, y1-5, x2+6, y2+5,
                                 fill="#26364b", outline="#8195af",
                                 tags=(tag, "pinned_box") if tag != "tooltip" else tag)
        c.tag_lower(box, text)

    def pin_deltas(self, info):
        # Chronological predecessor, regardless of click order or visibility.
        # Pins on different traces at the same time share the earlier reference.
        compatible = lambda p: p[0] == info[0] and (p[0] == "time" or p[1] == info[1])
        position = lambda p: p[3].timestamp if p[0] == "time" else p[2]
        earlier = [p for p in self.pinned_points.values()
                   if compatible(p) and position(p) < position(info)]
        if not earlier:
            return []
        reference = max(earlier, key=position)
        lines = ["", "Delta"]
        if info[0] == "time":
            sample, base = info[3], reference[3]
            lines.extend([f"From: {self.format_time(base.timestamp, full=True)}",
                          f"Time: {format_duration(sample.timestamp-base.timestamp)}",
                          f"Raw offset: {(sample.frequency-sample.nominal)-(base.frequency-base.nominal):+.9g} Hz"])
            if sample.controller_frequency is not None and base.controller_frequency is not None:
                lines.append(f"Frequency offset: {(sample.controller_frequency-sample.nominal)-(base.controller_frequency-base.nominal):+.9g} Hz")
            if sample.control_voltage_v is not None and base.control_voltage_v is not None:
                lines.append(f"Voltage: {(sample.control_voltage_v-base.control_voltage_v)*1000:+.9g} mV")
            else:
                lines.append("Voltage: Unavailable")
            if sample.temperature_c is not None and base.temperature_c is not None:
                delta = sample.temperature_c-base.temperature_c
                if self.temperature_unit.get() == "°F":
                    delta *= 9/5
                lines.append(f"Temperature: {delta:+.6g} {self.temperature_unit.get()}")
            else:
                lines.append("Temperature: Unavailable")
            if info[2] != reference[2]:
                lines.append("Across different continuous runs")
            if sample.nominal != base.nominal:
                lines.append("Nominal frequencies differ")
        else:
            lines.extend([f"From tau: {reference[2]:g} s",
                          f"Averaging time: {info[2]-reference[2]:+g} s",
                          f"Deviation: {info[3]-reference[3]:+.8e}"])
            if reference[3] > 0:
                lines.append(f"Deviation ratio: {info[3]/reference[3]:.6g}x")
        return lines

    def clear(self):
        self.label_positions.clear()
        self.pinned_points.clear()
        self.pinned_matches.clear()
        self.end_pan()
        self.hide_tooltip()
        self.zoom_limits.clear()
        if self.time_range.get() == "Custom":
            self.time_range.set("All data")
        self.follow_live.set(True)
        self.analysis = Analysis(max_ppm=self.analysis.max_ppm)
        self.stats.set("Waiting for frequency measurements")
        self.dirty = True

    def record_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        self.stop_record()
        try:
            self.record = open(path, "w", newline="", encoding="utf-8")
            self.writer = csv.writer(self.record)
            self.writer.writerow(FIELDS)
            self.record_started = time.monotonic()
            self.record_path = path
            self.record_count = 0
            self.update_record_status()
        except OSError as error:
            self.stop_record()
            messagebox.showerror("Recording", str(error))

    def stop_record(self):
        if self.record:
            self.record.close()
        self.record = self.writer = None
        self.record_started = None
        self.update_record_status()

    def update_record_status(self):
        if self.record_started is None:
            self.record_status.set("Not recording")
            return
        elapsed = int(time.monotonic()-self.record_started)
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.record_status.set(f"RECORDING • {Path(self.record_path).name} • "
                               f"{hours:02d}:{minutes:02d}:{seconds:02d} • {self.record_count:,} samples")

    def open_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not path:
            return
        self.disconnect()
        self.stop_record()
        self.clear()
        try:
            with open(path, newline="", encoding="utf-8-sig") as source:
                for row in csv.DictReader(source):
                    self.accept(Sample.parse(row, row.get("eventId", "")))
            self.status.set(f"Loaded {path}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("CSV import", str(error))
        self.draw()

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                with open(path, "w", newline="", encoding="utf-8") as target:
                    writer = csv.writer(target)
                    kind = self.view.get()
                    if kind != "Time plot":
                        writer.writerow(["tau_seconds", kind.lower(), "overlapping_terms"])
                        writer.writerows(self.analysis.curve(kind))
                        return
                    selected = self.selected_traces()
                    controller_columns = (["controllerFrequencyHz", "controllerFilterLevel"]
                                          if "Frequency" in selected else [])
                    writer.writerow(["endTimeUnixMs", *(TIME_VIEWS[name][1] for name in selected),
                                     *controller_columns, "run"])
                    writer.writerows((s.timestamp, *(TIME_VIEWS[name][2](s) for name in selected),
                                      *([s.controller_frequency, s.controller_level] if controller_columns else []),
                                      run)
                                     for run, s in self.analysis.samples)
            except OSError as error:
                messagebox.showerror("Export", str(error))

    def close(self):
        self.disconnect()
        self.stop_record()
        self.root.destroy()

    def save_image(self):
        path = filedialog.asksaveasfilename(defaultextension=".svg",
            filetypes=[("SVG image", "*.svg")], initialfile="nggpsdo-graph.svg")
        if path:
            try:
                save_svg(self.canvas, path, f"NGGPSDO {self.view.get()} — v{__version__}")
            except (OSError, tk.TclError) as error:
                messagebox.showerror("Save graph image", str(error))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
