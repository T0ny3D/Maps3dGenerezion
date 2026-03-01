from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.blender_backend import estimate_relief_mm
from ..core.dem_downloader import download_srtm_dem_for_bbox
from ..core.pipeline import (
    GenerateConfig,
    compute_gpx_bbox_lonlat,
    default_dem_output_path_for_gpx,
    run_pipeline,
)
from .preview3d import Preview3DWidget

QUALITY_TO_GRID = {"fast": 200, "high": 400, "ultra": 700}
PRINTER_PROFILE_TO_VALUES = {"bambu": (0.22, 0.9), "voron": (0.28, 1.0)}


class Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            out = self.fn(*self.args, **self.kwargs)
            self.finished.emit(out)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GPX to 3D STL / 3MF")
        self.setMinimumSize(1280, 820)
        self.setMinimumWidth(760)

        self._thread: QThread | None = None
        self._last_output_base: Path | None = None

        # Input path widgets
        self.gpx_path = QLineEdit()
        self.dem_path = QLineEdit()
        self.blender_exe_path = QLineEdit()

        # Output folder (zero-manual)
        self.out_dir = QLineEdit()
        self.out_dir.setPlaceholderText("Cartella output (vuota = cartella del GPX)")

        # Backend and quality
        self.backend = QComboBox()
        self.backend.addItem("Blender (consigliato)", userData="blender")
        self.backend.addItem("Python", userData="python")
        idx = self.backend.findData("blender")
        if idx >= 0:
            self.backend.setCurrentIndex(idx)

        self.quality = QComboBox()
        self.quality.addItem("Fast", userData="fast")
        self.quality.addItem("High", userData="high")
        self.quality.addItem("Ultra", userData="ultra")
        self.quality.setCurrentIndex(1)

        # Printer profile
        self.printer_profile = QComboBox()
        self.printer_profile.addItem("BambuLab", userData="bambu")
        self.printer_profile.addItem("Voron 2.4", userData="voron")
        self.printer_profile.addItem("Personalizzato", userData="custom")
        self.printer_profile.currentIndexChanged.connect(self._apply_printer_profile_defaults)

        # 3MF export option (Bambu)
        self.export_3mf = QCheckBox("Genera anche 3MF (Bambu)")
        self.export_3mf.setChecked(True)

        # Frame options
        self.separate_frame = QCheckBox()
        self.separate_frame.setChecked(True)
        self.frame_text_enabled = QCheckBox()
        self.frame_text_enabled.setChecked(True)

        self.flush_mode = QComboBox()
        self.flush_mode.addItem("Incassata", userData="recessed")

        # AMS and track options
        self.ams_enabled = QCheckBox()
        self.ams_enabled.setChecked(True)
        self.track_inlay_enabled = QCheckBox()
        self.track_inlay_enabled.setChecked(True)

        # Preview checkboxes
        self.show_frame = QCheckBox("Mostra cornice")
        self.show_frame.setChecked(True)
        self.show_ams = QCheckBox("Mostra layer AMS")
        self.show_ams.setChecked(True)
        self.show_track = QCheckBox("Mostra traccia")
        self.show_track.setChecked(True)

        # Text labels
        self.title_text = QLineEdit("")
        self.subtitle_text = QLineEdit("")
        self.label_n = QLineEdit("N")
        self.label_s = QLineEdit("S")
        self.label_e = QLineEdit("E")
        self.label_w = QLineEdit("O")

        # Dimension and sizing parameters (FIXED 120x120)
        self.size_x = QLineEdit("120")
        self.size_y = QLineEdit("120")
        self.base_mm = QLineEdit("5")
        self.vertical_scale = QLineEdit("1.0")
        self.track_height = QLineEdit("2")

        # Frame parameters
        self.frame_wall_mm = QLineEdit("10")
        self.frame_height_mm = QLineEdit("8")
        self.lip_depth_mm = QLineEdit("3")
        self.clearance_mm = QLineEdit("0.3")
        self.recess_mm = QLineEdit("1.5")
        self.lead_in_mm = QLineEdit("1.0")
        self.finger_notch_radius_mm = QLineEdit("7.0")
        self.rim_mm = QLineEdit("3.0")

        # Groove and track parameters
        self.groove_width_mm = QLineEdit("2.6")
        self.groove_depth_mm = QLineEdit("1.6")
        self.groove_chamfer_mm = QLineEdit("0.4")
        self.track_clearance_mm = QLineEdit("0.20")
        self.track_relief_mm = QLineEdit("0.6")
        self.track_top_radius_mm = QLineEdit("0.8")

        # Text rendering options
        self.text_mode = QComboBox()
        self.text_mode.addItem("Inciso", userData="inciso")
        self.text_mode.addItem("Rilievo", userData="rilievo")
        self.text_depth_mm = QLineEdit("1.2")

        # Test mode
        self.test_mode = QCheckBox()
        self.test_mode.setChecked(False)
        self.test_mode_info = QLabel("")
        self.test_mode.toggled.connect(self._on_test_mode_toggled)

        # Buttons
        self.download_dem_btn = QPushButton("Scarica DEM (SRTM 30m)")
        self.download_dem_btn.setEnabled(False)
        self.download_dem_btn.clicked.connect(self._download_dem)

        self.preview_btn = QPushButton("Carica anteprima 3D")
        self.preview_btn.clicked.connect(self._load_preview_from_outputs)

        self.generate_btn = QPushButton("Genera")
        self.generate_btn.setMinimumHeight(48)
        self.generate_btn.clicked.connect(self._generate)

        # Progress and status
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.relief_estimate = QLabel("Rilievo massimo stimato (mm): -")
        self.status = QLabel("Pronto")

        # Log and preview widgets
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.preview = Preview3DWidget()

        self._build_ui()
        self._apply_printer_profile_defaults()
        self._auto_detect_blender_exe()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        layout = QHBoxLayout(root)

        left = self._build_controls_column()
        right = self._build_preview_column()

        layout.addWidget(left, 4)
        layout.addWidget(right, 6)

    def _build_controls_column(self) -> QWidget:
        container = QWidget()
        l = QVBoxLayout(container)

        # Input section
        files = QGroupBox("Input")
        f = QFormLayout(files)

        gpx_row = QHBoxLayout()
        gpx_row.addWidget(self.gpx_path)
        gpx_btn = QPushButton("Apri GPX")
        gpx_btn.clicked.connect(self._select_gpx)
        gpx_row.addWidget(gpx_btn)

        dem_row = QHBoxLayout()
        dem_row.addWidget(self.dem_path)
        dem_btn = QPushButton("Apri DEM (GeoTIFF)")
        dem_btn.clicked.connect(self._select_dem)
        dem_row.addWidget(dem_btn)

        blender_row = QHBoxLayout()
        blender_row.addWidget(self.blender_exe_path)
        blender_btn = QPushButton("Sfoglia")
        blender_btn.clicked.connect(self._select_blender_exe)
        blender_row.addWidget(blender_btn)

        out_row = QHBoxLayout()
        out_row.addWidget(self.out_dir)
        out_btn = QPushButton("Sfoglia")
        out_btn.clicked.connect(self._select_out_dir)
        out_row.addWidget(out_btn)

        f.addRow("GPX:", gpx_row)
        f.addRow("DEM:", dem_row)
        f.addRow("", self.download_dem_btn)
        f.addRow("Output:", out_row)
        f.addRow("Backend:", self.backend)
        f.addRow("Qualità:", self.quality)
        f.addRow("Stampante:", self.printer_profile)
        f.addRow("", self.export_3mf)
        f.addRow("Blender.exe:", blender_row)
        l.addWidget(files)

        # Parameters section
        params = QGroupBox("Parametri")
        p = QFormLayout(params)
        p.addRow("Dimensione X (mm):", self.size_x)
        p.addRow("Dimensione Y (mm):", self.size_y)
        p.addRow("Spessore base (mm):", self.base_mm)
        p.addRow("Scala verticale:", self.vertical_scale)
        p.addRow("Altezza traccia legacy:", self.track_height)
        p.addRow("Rilievo stimato:", self.relief_estimate)
        l.addWidget(params)

        # Frame section
        frame = QGroupBox("Cornice / Incastro")
        fr = QFormLayout(frame)
        fr.addRow("Cornice separata:", self.separate_frame)
        fr.addRow("Modalità:", self.flush_mode)
        fr.addRow("Testi cornice:", self.frame_text_enabled)
        fr.addRow("frame_wall_mm:", self.frame_wall_mm)
        fr.addRow("frame_height_mm:", self.frame_height_mm)
        fr.addRow("lip_depth_mm:", self.lip_depth_mm)
        fr.addRow("clearance_mm:", self.clearance_mm)
        fr.addRow("recess_mm:", self.recess_mm)
        fr.addRow("lead_in_mm:", self.lead_in_mm)
        fr.addRow("finger_notch_radius_mm:", self.finger_notch_radius_mm)
        fr.addRow("rim_mm:", self.rim_mm)
        fr.addRow("Test incastro:", self.test_mode)
        fr.addRow("", self.test_mode_info)
        l.addWidget(frame)

        # AMS and track section
        ams = QGroupBox("AMS + Traccia")
        a = QFormLayout(ams)
        a.addRow("AMS 4 colori:", self.ams_enabled)
        a.addRow("Traccia inlay:", self.track_inlay_enabled)
        a.addRow("groove_width_mm:", self.groove_width_mm)
        a.addRow("groove_depth_mm:", self.groove_depth_mm)
        a.addRow("groove_chamfer_mm:", self.groove_chamfer_mm)
        a.addRow("track_clearance_mm:", self.track_clearance_mm)
        a.addRow("track_relief_mm:", self.track_relief_mm)
        a.addRow("track_top_radius_mm:", self.track_top_radius_mm)
        l.addWidget(ams)

        # Text section
        text = QGroupBox("Testi")
        t = QFormLayout(text)
        t.addRow("Titolo:", self.title_text)
        t.addRow("Sottotitolo:", self.subtitle_text)
        t.addRow("N:", self.label_n)
        t.addRow("S:", self.label_s)
        t.addRow("E:", self.label_e)
        t.addRow("O:", self.label_w)
        t.addRow("Modalità testo:", self.text_mode)
        t.addRow("text_depth_mm:", self.text_depth_mm)
        l.addWidget(text)

        l.addWidget(self.status)
        l.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        return scroll

    def _build_preview_column(self) -> QWidget:
        right = QWidget()
        r = QVBoxLayout(right)

        toggles = QHBoxLayout()
        toggles.addWidget(self.preview_btn)
        toggles.addWidget(self.show_frame)
        toggles.addWidget(self.show_ams)
        toggles.addWidget(self.show_track)
        toggles.addStretch(1)

        r.addLayout(toggles)
        r.addWidget(self.preview, 7)
        r.addWidget(self.log, 3)

        bottom = QHBoxLayout()
        bottom.addWidget(self.progress, 4)
        bottom.addWidget(self.generate_btn, 2)
        r.addLayout(bottom)
        return right

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _select_out_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Seleziona cartella output")
        if path:
            self.out_dir.setText(path)

    def _select_gpx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona GPX", "", "GPX files (*.gpx)")
        if not path:
            return

        self.gpx_path.setText(path)
        self.download_dem_btn.setEnabled(True)

        # Default output dir = folder del GPX (solo se non impostato)
        if not self.out_dir.text().strip():
            self.out_dir.setText(str(Path(path).parent))

        # Log bbox + point count
        try:
            from ..core.gpx_loader import load_gpx_lonlat

            points = load_gpx_lonlat(path)
            num_points = len(points)
            min_lon, min_lat, max_lon, max_lat = compute_gpx_bbox_lonlat(path, margin_ratio=0.20)
            self._append_log(
                f"GPX caricato: {num_points} punti, bbox=[{min_lon:.4f}, {min_lat:.4f}, {max_lon:.4f}, {max_lat:.4f}]"
            )
        except Exception as exc:  # noqa: BLE001
            try:
                min_lon, min_lat, max_lon, max_lat = compute_gpx_bbox_lonlat(path, margin_ratio=0.20)
                self._append_log(
                    f"GPX caricato, bbox=[{min_lon:.4f}, {min_lat:.4f}, {max_lon:.4f}, {max_lat:.4f}] (punti non letti: {exc})"
                )
            except Exception as exc2:  # noqa: BLE001
                self._append_log(f"Errore lettura GPX/bbox: {exc2}")

        # Auto-download DEM if not set
        if not self.dem_path.text().strip():
            self._append_log("DEM non impostato: avvio download automatico...")
            self._download_dem()

    def _select_dem(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona DEM", "", "GeoTIFF (*.tif *.tiff)")
        if path:
            self.dem_path.setText(path)

    def _select_blender_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona blender.exe", "", "Executable (*.exe);;All files (*.*)")
        if path:
            self.blender_exe_path.setText(path)

    def _auto_detect_blender_exe(self) -> None:
        """Auto-detect blender.exe on Windows common paths if not already set."""
        if self.blender_exe_path.text().strip():
            return

        import os
        import platform
        import glob

        if platform.system() != "Windows":
            return

        direct_paths = [
            r"C:\Program Files\Blender Foundation\Blender\blender.exe",
            r"C:\Program Files (x86)\Blender Foundation\Blender\blender.exe",
        ]

        wildcard_patterns = [
            r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
            r"C:\Program Files (x86)\Blender Foundation\Blender*\blender.exe",
        ]

        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            wildcard_patterns.append(rf"{localappdata}\Programs\Blender Foundation\Blender*\blender.exe")

        for p in direct_paths:
            if Path(p).exists():
                self.blender_exe_path.setText(p)
                self._append_log(f"Blender rilevato: {p}")
                return

        for pat in wildcard_patterns:
            matches = glob.glob(pat)
            if matches:
                found = sorted(matches)[-1]
                self.blender_exe_path.setText(found)
                self._append_log(f"Blender rilevato: {found}")
                return

        self._append_log("Blender non rilevato automaticamente. Usa 'Sfoglia' per selezionarlo.")

    def _run_background(self, fn, on_done, what: str) -> None:
        if self._thread is not None:
            QMessageBox.warning(self, "Operazione in corso", "Attendere il completamento dell'operazione corrente.")
            return

        self.progress.setRange(0, 0)
        self.generate_btn.setEnabled(False)
        self.download_dem_btn.setEnabled(False)
        self._append_log(f"[{what}] avvio...")

        self._thread = QThread(self)
        worker = Worker(fn)
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.finished.connect(lambda data: self._on_worker_done(data, on_done, what))
        worker.failed.connect(lambda err: self._on_worker_error(err, what))
        worker.finished.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self) -> None:
        self._thread = None
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.generate_btn.setEnabled(True)
        self.download_dem_btn.setEnabled(bool(self.gpx_path.text().strip()))

    def _on_worker_done(self, data, on_done, what: str) -> None:
        self._append_log(f"[{what}] completato")
        on_done(data)

    def _on_worker_error(self, err: str, what: str) -> None:
        self._append_log(f"[{what}] errore: {err}")
        self.status.setText("Errore")
        QMessageBox.critical(self, f"Errore {what}", err)

    def _download_dem(self) -> None:
        gpx = self.gpx_path.text().strip()
        if not gpx:
            QMessageBox.warning(self, "GPX mancante", "Seleziona prima un GPX.")
            return
        self.status.setText("Scarico DEM SRTM...")

        def task():
            min_lon, min_lat, max_lon, max_lat = compute_gpx_bbox_lonlat(gpx, margin_ratio=0.20)
            out_dem = default_dem_output_path_for_gpx(gpx)
            try:
                return download_srtm_dem_for_bbox(min_lon, min_lat, max_lon, max_lat, out_dem)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"Download DEM fallito.\n"
                    f"bbox=[{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}]\n"
                    f"output atteso: {out_dem}\n"
                    f"Dettaglio: {exc}"
                ) from exc

        def done(path: Path) -> None:
            self.dem_path.setText(str(path))
            self.status.setText("Download DEM completato")
            QMessageBox.information(self, "DEM scaricato", f"DEM salvato in:\n{path}")

        self._run_background(task, done, "download DEM")

    def _on_test_mode_toggled(self, enabled: bool) -> None:
        if enabled:
            self.test_mode_info.setText("Verranno generati due STL di test: test_map e test_frame")
            self.frame_text_enabled.setChecked(False)
        else:
            self.test_mode_info.setText("")

    def _apply_printer_profile_defaults(self, *_: object) -> None:
        profile = str(self.printer_profile.currentData())
        values = PRINTER_PROFILE_TO_VALUES.get(profile)
        if values is None:
            return
        clearance, lead_in = values
        self.clearance_mm.setText(f"{clearance:.2f}")
        self.lead_in_mm.setText(f"{lead_in:.1f}")

    def _build_config(self) -> GenerateConfig:
        return GenerateConfig(
            model_width_mm=float(self.size_x.text()),
            model_height_mm=float(self.size_y.text()),
            base_thickness_mm=float(self.base_mm.text()),
            vertical_scale=float(self.vertical_scale.text()),
            track_height_mm=float(self.track_height.text()),
            grid_res=QUALITY_TO_GRID.get(str(self.quality.currentData()), 400),
            separate_frame=self.separate_frame.isChecked(),
            frame_text_enabled=self.frame_text_enabled.isChecked(),
            frame_wall_mm=float(self.frame_wall_mm.text()),
            frame_height_mm=float(self.frame_height_mm.text()),
            lip_depth_mm=float(self.lip_depth_mm.text()),
            clearance_mm=float(self.clearance_mm.text()),
            text_mode=str(self.text_mode.currentData()),
            text_depth_mm=float(self.text_depth_mm.text()),
            title_text=self.title_text.text(),
            subtitle_text=self.subtitle_text.text(),
            label_n=self.label_n.text() or "N",
            label_s=self.label_s.text() or "S",
            label_e=self.label_e.text() or "E",
            label_w=self.label_w.text() or "O",
            flush_mode=str(self.flush_mode.currentData()),
            recess_mm=float(self.recess_mm.text()),
            lead_in_mm=float(self.lead_in_mm.text()),
            finger_notch_radius_mm=float(self.finger_notch_radius_mm.text()),
            rim_mm=float(self.rim_mm.text()),
            printer_profile=str(self.printer_profile.currentData()),
            test_mode=self.test_mode.isChecked(),
            test_size_mm=40.0,
            ams_enabled=self.ams_enabled.isChecked(),
            track_inlay_enabled=self.track_inlay_enabled.isChecked(),
            groove_width_mm=float(self.groove_width_mm.text()),
            groove_depth_mm=float(self.groove_depth_mm.text()),
            groove_chamfer_mm=float(self.groove_chamfer_mm.text()),
            track_clearance_mm=float(self.track_clearance_mm.text()),
            track_relief_mm=float(self.track_relief_mm.text()),
            track_top_radius_mm=float(self.track_top_radius_mm.text()),
        )

    def _collect_preview_paths(
        self, base_out: Path, config: GenerateConfig
    ) -> list[tuple[Path, tuple[float, float, float, float], str]]:
        stem = base_out.stem
        prefix = "_test_" if config.test_mode else "_"

        out = [
            (base_out.with_name(stem + prefix + "base_brown.stl"), (0.47, 0.31, 0.18, 1.0), "base"),
        ]

        if self.show_ams.isChecked():
            out.extend(
                [
                    (base_out.with_name(stem + prefix + "water.stl"), (0.2, 0.45, 0.95, 0.95), "water"),
                    (base_out.with_name(stem + prefix + "green.stl"), (0.18, 0.70, 0.25, 0.95), "green"),
                    (base_out.with_name(stem + prefix + "detail.stl"), (0.9, 0.87, 0.78, 0.95), "detail"),
                ]
            )

        if self.show_track.isChecked():
            out.append((base_out.with_name(stem + prefix + "track_inlay_red.stl"), (0.9, 0.1, 0.1, 1.0), "track"))

        if config.separate_frame and self.show_frame.isChecked():
            out.append((base_out.with_name(stem + prefix + "frame.stl"), (0.6, 0.6, 0.62, 0.9), "frame"))

        return out

    def _load_preview_from_outputs(self) -> None:
        if self._last_output_base is None:
            QMessageBox.information(self, "Anteprima", "Genera prima almeno un set STL o seleziona output durante la generazione.")
            return

        cfg = self._build_config()
        self.preview.clear()
        for path, color, name in self._collect_preview_paths(self._last_output_base, cfg):
            if not path.exists():
                self._append_log(f"anteprima: file non trovato ({name}) -> {path}")
                continue
            try:
                self.preview.load_stl(path, color=color)
                self._append_log(f"anteprima: caricato {name}")
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"anteprima: errore su {name}: {exc}")
        self.preview.frame_all()

    def _generate(self) -> None:
        gpx = self.gpx_path.text().strip()
        dem = self.dem_path.text().strip()
        if not gpx or not dem:
            QMessageBox.warning(self, "Input mancanti", "Seleziona sia GPX che DEM.")
            return

        # output base automatico
        out_dir = Path(self.out_dir.text().strip() or Path(gpx).parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_base = out_dir / Path(gpx).stem
        output_path = str(output_base.with_suffix(".stl"))

        self._append_log(f"Output dir: {out_dir}")
        self._append_log(f"Output base: {output_base}")

        config = self._build_config()
        try:
            relief_mm = estimate_relief_mm(gpx, dem, config)
            self.relief_estimate.setText(f"Rilievo massimo stimato (mm): {relief_mm:.2f}")
            if relief_mm > 60.0:
                QMessageBox.warning(
                    self,
                    "Warning rilievo alto",
                    f"Rilievo stimato: {relief_mm:.2f} mm (> 60 mm).\nLa generazione continua comunque.",
                )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"stima rilievo non disponibile: {exc}")

        backend_value = str(self.backend.currentData())
        blender_path = self.blender_exe_path.text().strip() or None

        if backend_value == "blender" and (not blender_path or not Path(blender_path).exists()):
            reply = QMessageBox.question(
                self,
                "Blender non trovato",
                "Blender non trovato. Seleziona blender.exe oppure usa il backend Python.\nUsare il backend Python per questa generazione?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                backend_value = "python"
            else:
                return

        self.status.setText(f"Generazione in corso ({backend_value})...")

        export_3mf_enabled = self.export_3mf.isChecked()

        def task() -> tuple[Path, Path | None, str | None]:
            try:
                run_pipeline(
                    gpx_path=gpx,
                    dem_path=dem,
                    stl_output_path=output_path,
                    config=config,
                    backend=backend_value,
                    blender_exe_path=blender_path,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Errore pipeline [{backend_value}]: {exc}") from exc

            out_base = Path(output_path)

            out_3mf: Path | None = None
            export_err: str | None = None
            if export_3mf_enabled:
                try:
                    from ..export_3mf import create_3mf_from_stl_output_base

                    out_3mf = create_3mf_from_stl_output_base(out_base, test_mode=config.test_mode)
                except Exception as exc:  # noqa: BLE001
                    export_err = str(exc)

            return out_base, out_3mf, export_err

        def done(result: tuple[Path, Path | None, str | None]) -> None:
            out_base, out_3mf, export_err = result
            self._last_output_base = out_base
            self.status.setText("Generazione completata")
            self._append_log(f"output base: {out_base}")

            if export_err is not None:
                self._append_log(f"Errore esportazione 3MF: {export_err}")
            elif out_3mf is not None:
                self._append_log(f"3MF esportato: {out_3mf}")

            self._load_preview_from_outputs()

        self._run_background(task, done, "generazione")