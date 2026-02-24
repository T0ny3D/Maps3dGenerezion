from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.blender_backend import estimate_relief_mm
from ..core.pipeline import GenerateConfig, run_pipeline

QUALITY_TO_GRID = {"fast": 200, "high": 400, "ultra": 700}
PRINTER_PROFILE_TO_VALUES = {"bambu": (0.22, 0.9), "voron": (0.28, 1.0)}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GPX to 3D STL")
        self.setMinimumWidth(760)

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
        self.test_mode.setChecked(False)
        self.test_mode_info = QLabel("")

        self.relief_estimate = QLabel("Rilievo massimo stimato (mm): -")

        self._build_ui()
        self._apply_printer_profile_defaults()
        self.test_mode.toggled.connect(self._on_test_mode_toggled)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

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

        form = QFormLayout()
        form.addRow("GPX:", gpx_row)
        form.addRow("DEM:", dem_row)
        form.addRow("Backend:", self.backend)
        form.addRow("Qualità:", self.quality)
        form.addRow("Stampante:", self.printer_profile)
        form.addRow("Percorso Blender.exe (opzionale):", blender_row)

        form.addRow("AMS 4 colori mappa:", self.ams_enabled)
        form.addRow("Traccia rossa a incastro:", self.track_inlay_enabled)
        form.addRow("Cornice separata:", self.separate_frame)
        form.addRow("Test incastro (40×40 mm):", self.test_mode)
        form.addRow("Modalità cornice:", self.flush_mode)
        form.addRow("Testi sulla cornice:", self.frame_text_enabled)
        form.addRow("Titolo:", self.title_text)
        form.addRow("Sottotitolo:", self.subtitle_text)
        form.addRow("Etichetta N:", self.label_n)
        form.addRow("Etichetta S:", self.label_s)
        form.addRow("Etichetta E:", self.label_e)
        form.addRow("Etichetta O:", self.label_w)

        form.addRow("Dimensione X (mm):", self.size_x)
        form.addRow("Dimensione Y (mm):", self.size_y)
        form.addRow("Spessore base (mm):", self.base_mm)
        form.addRow("Scala verticale:", self.vertical_scale)
        form.addRow("Altezza traccia legacy (mm):", self.track_height)

        form.addRow("groove_width_mm:", self.groove_width_mm)
        form.addRow("groove_depth_mm:", self.groove_depth_mm)
        form.addRow("groove_chamfer_mm:", self.groove_chamfer_mm)
        form.addRow("track_clearance_mm:", self.track_clearance_mm)
        form.addRow("track_relief_mm:", self.track_relief_mm)
        form.addRow("track_top_radius_mm:", self.track_top_radius_mm)

        form.addRow("frame_wall_mm:", self.frame_wall_mm)
        form.addRow("frame_height_mm:", self.frame_height_mm)
        form.addRow("lip_depth_mm:", self.lip_depth_mm)
        form.addRow("clearance_mm:", self.clearance_mm)
        form.addRow("recess_mm:", self.recess_mm)
        form.addRow("lead_in_mm:", self.lead_in_mm)
        form.addRow("finger_notch_radius_mm:", self.finger_notch_radius_mm)
        form.addRow("rim_mm:", self.rim_mm)

        form.addRow("Modalità testo:", self.text_mode)
        form.addRow("text_depth_mm:", self.text_depth_mm)

        generate_btn = QPushButton("Genera STL")
        generate_btn.clicked.connect(self._generate)

        self.status = QLabel("Pronto")
        layout.addLayout(form)
        layout.addWidget(self.relief_estimate)
        layout.addWidget(self.test_mode_info)
        layout.addWidget(generate_btn)
        layout.addWidget(self.status)

    def _select_gpx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona GPX", "", "GPX files (*.gpx)")
        if path:
            self.gpx_path.setText(path)

    def _select_dem(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona DEM", "", "GeoTIFF (*.tif *.tiff)")
        if path:
            self.dem_path.setText(path)

    def _select_blender_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona blender.exe", "", "Executable (*.exe);;All files (*.*)")
        if path:
            self.blender_exe_path.setText(path)

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

    def _generate(self) -> None:
        try:
            gpx = self.gpx_path.text().strip()
            dem = self.dem_path.text().strip()
            if not gpx or not dem:
                raise ValueError("Seleziona sia GPX che DEM.")

            config = self._build_config()
            relief_mm = estimate_relief_mm(gpx, dem, config)
            self.relief_estimate.setText(f"Rilievo massimo stimato (mm): {relief_mm:.2f}")
            if relief_mm > 60.0:
                QMessageBox.warning(self, "Warning rilievo alto", f"Rilievo massimo stimato elevato: {relief_mm:.2f} mm (> 60 mm).\nLa generazione continua comunque.")

            default_name = f"{Path(gpx).stem}.stl"
            output_path, _ = QFileDialog.getSaveFileName(self, "Salva base nome STL", default_name, "STL (*.stl)")
            if not output_path:
                return

            backend_value = str(self.backend.currentData())
            blender_path = self.blender_exe_path.text().strip() or None
            self.status.setText(f"Generazione in corso... backend: {backend_value}")

            run_pipeline(gpx_path=gpx, dem_path=dem, stl_output_path=output_path, config=config, backend=backend_value, blender_exe_path=blender_path)

            base_out = Path(output_path)
            suffix = "_test" if config.test_mode else ""
            msg = (
                f"MAP BASE (brown):\n{base_out.with_name(base_out.stem + suffix + '_base_brown.stl')}"
                f"\nWATER: \n{base_out.with_name(base_out.stem + suffix + '_water.stl')}"
                f"\nGREEN: \n{base_out.with_name(base_out.stem + suffix + '_green.stl')}"
                f"\nDETAIL: \n{base_out.with_name(base_out.stem + suffix + '_detail.stl')}"
                f"\nTRACK INLAY RED: \n{base_out.with_name(base_out.stem + suffix + '_track_inlay_red.stl')}"
            )
            if config.separate_frame:
                msg += f"\n\nFRAME STL:\n{base_out.with_name(base_out.stem + suffix + '_frame.stl')}"
            self.status.setText("Generazione completata")
            QMessageBox.information(self, "Completato", msg)
        except Exception as exc:  # noqa: BLE001
            self.status.setText("Errore")
            QMessageBox.critical(self, "Errore", str(exc))
