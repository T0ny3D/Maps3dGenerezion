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
from ..core.pipeline import GenerateConfig, compute_gpx_bbox_lonlat, default_dem_output_path_for_gpx, run_pipeline
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
        self.setWindowTitle("GPX to 3D STL")
        self.setMinimumSize(1280, 820)

        self._thread: QThread | None = None
        self._last_output_base: Path | None = None

        self.gpx_path = QLineEdit()
        self.dem_path = QLineEdit()
        self.backend = QComboBox()
        self.backend.addItem("Blender (consigliato)", userData="blender")
        self.backend.addItem("Python", userData="python")
        self.blender_exe_path = QLineEdit()

        self.quality = QComboBox()
        self.quality.addItem("Fast", userData="fast")
        self.quality.addItem("High", userData="high")
        self.quality.addItem("Ultra", userData="ultra")
        self.quality.setCurrentIndex(1)

        self.printer_profile = QComboBox()
        self.printer_profile.addItem("BambuLab", userData="bambu")
        self.printer_profile.addItem("Voron 2.4", userData="voron")
        self.printer_profile.addItem("Personalizzato", userData="custom")
        self.printer_profile.currentIndexChanged.connect(self._apply_printer_profile_defaults)

        self.separate_frame = QCheckBox()
        self.separate_frame.setChecked(True)
        self.frame_text_enabled = QCheckBox()
        self.frame_text_enabled.setChecked(True)
        self.flush_mode = QComboBox()
        self.flush_mode.addItem("Incassata", userData="recessed")

        self.ams_enabled = QCheckBox()
        self.ams_enabled.setChecked(True)
        self.track_inlay_enabled = QCheckBox()
        self.track_inlay_enabled.setChecked(True)

        self.show_frame = QCheckBox("Mostra cornice")
        self.show_frame.setChecked(True)
        self.show_ams = QCheckBox("Mostra layer AMS")
        self.show_ams.setChecked(True)
        self.show_track = QCheckBox("Mostra traccia")
        self.show_track.setChecked(True)

        self.title_text = QLineEdit("")
        self.subtitle_text = QLineEdit("")
        self.label_n = QLineEdit("N")
        self.label_s = QLineEdit("S")
        self.label_e = QLineEdit("E")
        self.label_w = QLineEdit("O")

        self.size_x = QLineEdit("150")
        self.size_y = QLineEdit("150")
        self.base_mm = QLineEdit("5")
        self.vertical_scale = QLineEdit("1.0")
        self.track_height = QLineEdit("2")

        self.frame_wall_mm = QLineEdit("10")
        self.frame_height_mm = QLineEdit("8")
        self.lip_depth_mm = QLineEdit("3")
        self.clearance_mm = QLineEdit("0.3")
        self.recess_mm = QLineEdit("1.5")
        self.lead_in_mm = QLineEdit("1.0")
        self.finger_notch_radius_mm = QLineEdit("7.0")
        self.rim_mm = QLineEdit("3.0")

        self.groove_width_mm = QLineEdit("2.6")
        self.groove_depth_mm = QLineEdit("1.6")
        self.groove_chamfer_mm = QLineEdit("0.4")
        self.track_clearance_mm = QLineEdit("0.20")
        self.track_relief_mm = QLineEdit("0.6")
        self.track_top_radius_mm = QLineEdit("0.8")

        self.text_mode = QComboBox()
        self.text_mode.addItem("Inciso", userData="inciso")
        self.text_mode.addItem("Rilievo", userData="rilievo")
        self.text_depth_mm = QLineEdit("1.2")

        self.test_mode = QCheckBox()
        self.test_mode_info = QLabel("")

        self.download_dem_btn = QPushButton("Scarica DEM (SRTM 30m)")
        self.download_dem_btn.setEnabled(False)
        self.download_dem_btn.clicked.connect(self._download_dem)

        self.preview_btn = QPushButton("Carica anteprima 3D")
        self.preview_btn.clicked.connect(self._load_preview_from_outputs)

        self.generate_btn = QPushButton("Genera STL")
        self.generate_btn.setMinimumHeight(48)
        self.generate_btn.clicked.connect(self._generate)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.relief_estimate = QLabel("Rilievo massimo stimato (mm): -")
        self.status = QLabel("Pronto")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.preview = Preview3DWidget()

        self._build_ui()
        self._apply_printer_profile_defaults()
        self.test_mode.toggled.connect(self._on_test_mode_toggled)

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

        files = QGroupBox("Input")
        f = QFormLayout(files)
        gpx_row = QHBoxLayout()
        gpx_row.addWidget(self.gpx_path)
        gpx_btn = QPushButton("Apri GPX")
        gpx_btn.clicked.connect(self._select_gpx)
        gpx_row.addWidget(gpx_btn)

        dem_row = QHBoxLayout()
        dem_row.addWidget(self.dem_path)
        dem_btn = QPushButton("Apri DEM")
        dem_btn.clicked.connect(self._select_dem)
        dem_row.addWidget(dem_btn)

        blender_row = QHBoxLayout()
        blender_row.addWidget(self.blender_exe_path)
        blender_btn = QPushButton("Sfoglia")
        blender_btn.clicked.connect(self._select_blender_exe)
        blender_row.addWidget(blender_btn)

        f.addRow("GPX:", gpx_row)
        f.addRow("DEM:", dem_row)
        f.addRow("", self.download_dem_btn)
        f.addRow("Backend:", self.backend)
        f.addRow("Qualità:", self.quality)
        f.addRow("Stampante:", self.printer_profile)
        f.addRow("Blender.exe:", blender_row)
        l.addWidget(files)

        params = QGroupBox("Parametri")
        p = QFormLayout(params)
        p.addRow("Dimensione X (mm):", self.size_x)
        p.addRow("Dimensione Y (mm):", self.size_y)
        p.addRow("Spessore base (mm):", self.base_mm)
        p.addRow("Scala verticale:", self.vertical_scale)
        p.addRow("Altezza traccia legacy:", self.track_height)
        p.addRow("Rilievo stimato:", self.relief_estimate)
        l.addWidget(params)

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

    def _select_gpx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona GPX", "", "GPX files (*.gpx)")
        if path:
            self.gpx_path.setText(path)
            self.download_dem_btn.setEnabled(True)

    def _select_dem(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona DEM", "", "GeoTIFF (*.tif *.tiff)")
        if path:
            self.dem_path.setText(path)

    def _select_blender_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona blender.exe", "", "Executable (*.exe);;All files (*.*)")
        if path:
            self.blender_exe_path.setText(path)

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
            return download_srtm_dem_for_bbox(min_lon, min_lat, max_lon, max_lat, out_dem)

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

    def _collect_preview_paths(self, base_out: Path, config: GenerateConfig) -> list[tuple[Path, tuple[float, float, float, float], str]]:
        suffix = "_test" if config.test_mode else ""
        out = [
            (base_out.with_name(base_out.stem + suffix + "_base_brown.stl"), (0.47, 0.31, 0.18, 1.0), "base"),
        ]
        if self.show_ams.isChecked():
            out.extend(
                [
                    (base_out.with_name(base_out.stem + suffix + "_water.stl"), (0.2, 0.45, 0.95, 0.95), "water"),
                    (base_out.with_name(base_out.stem + suffix + "_green.stl"), (0.18, 0.70, 0.25, 0.95), "green"),
                    (base_out.with_name(base_out.stem + suffix + "_detail.stl"), (0.9, 0.87, 0.78, 0.95), "detail"),
                ]
            )
        if self.show_track.isChecked():
            out.append((base_out.with_name(base_out.stem + suffix + "_track_inlay_red.stl"), (0.9, 0.1, 0.1, 1.0), "track"))
        if config.separate_frame and self.show_frame.isChecked():
            out.append((base_out.with_name(base_out.stem + suffix + "_frame.stl"), (0.6, 0.6, 0.62, 0.9), "frame"))
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

        config = self._build_config()
        try:
            relief_mm = estimate_relief_mm(gpx, dem, config)
            self.relief_estimate.setText(f"Rilievo massimo stimato (mm): {relief_mm:.2f}")
            if relief_mm > 60.0:
                QMessageBox.warning(self, "Warning rilievo alto", f"Rilievo stimato: {relief_mm:.2f} mm (> 60 mm).")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"stima rilievo non disponibile: {exc}")

        default_name = f"{Path(gpx).stem}.stl"
        output_path, _ = QFileDialog.getSaveFileName(self, "Salva base nome STL", default_name, "STL (*.stl)")
        if not output_path:
            return

        backend_value = str(self.backend.currentData())
        blender_path = self.blender_exe_path.text().strip() or None
        self.status.setText(f"Generazione in corso ({backend_value})...")

        def task() -> Path:
            run_pipeline(gpx_path=gpx, dem_path=dem, stl_output_path=output_path, config=config, backend=backend_value, blender_exe_path=blender_path)
            return Path(output_path)

        def done(out_base: Path) -> None:
            self._last_output_base = out_base
            self.status.setText("Generazione completata")
            self._append_log(f"output base: {out_base}")
            self._load_preview_from_outputs()

        self._run_background(task, done, "generazione STL")
