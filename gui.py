from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from smart_lock.camera import Camera
from smart_lock.config import AppConfig, load_config
from smart_lock.face_id import create_face_authenticator
from smart_lock.fusion import FusionEngine
from smart_lock.liveness import create_liveness_checker
from smart_lock.lock_gpio import create_lock_actuator
from smart_lock.logging_utils import setup_logging
from smart_lock.results import AuthResult
from smart_lock.sensor import create_presence_sensor
from smart_lock.speaker_id import create_speaker_authenticator
from smart_lock.voice_feedback import VoiceFeedback
from smart_lock.auth_context import read_auth_context, write_auth_context
from smart_lock.event_reporting import report_auth_failure

LOGGER = logging.getLogger(__name__)


class SmartLockWindow(QtWidgets.QMainWindow):
    def __init__(self, config: AppConfig, force_lock_dry_run: bool = True) -> None:
        super().__init__()
        self._config = config
        self._camera = Camera(config.camera, config.system.dry_run)
        self._sensor = create_presence_sensor(config.sensor, config.system.dry_run)
        defer_unlock = config.lock.flow == "agent_confirm"
        self._lock = create_lock_actuator(
            config.lock,
            config.system.dry_run or force_lock_dry_run or defer_unlock,
        )
        self._fusion = FusionEngine(config.fusion)
        self._voice = VoiceFeedback(config.voice_feedback)

        self._face = None
        self._liveness = None
        self._speaker = None
        self._last_frame: np.ndarray | None = None
        self._last_sensor = False
        self._previous_sensor = False
        self._last_face = AuthResult("face", False, 0.0, "not checked")
        self._auth_in_progress = False
        self._camera_active = False
        self._last_auto_auth_at = 0.0
        self._last_presence_at = 0.0
        self._sensor_true_until = 0.0
        self._models_preloaded = False
        self._auto_auth_paused_until = 0.0
        self._face_pass_until = 0.0
        self._liveness_pass_until = 0.0
        self._speaker_pass_until = 0.0
        self._last_auth_passed = False
        self._last_agent_status = ""
        self._agent_running = False
        self._presence_lost_at = None
        self._unknown_face_since: float | None = None
        self._event_reported_for_presence = False
        self._last_event_report_attempt_at = 0.0

        self.setWindowTitle("Jetson 智能门锁 MVP")
        self.resize(1120, 720)
        self._build_ui()

        self._camera_timer = QtCore.QTimer(self)
        self._camera_timer.timeout.connect(self._update_camera)
        self._sensor_timer = QtCore.QTimer(self)
        self._sensor_timer.timeout.connect(self._update_sensor)
        self._agent_status_timer = QtCore.QTimer(self)
        self._agent_status_timer.timeout.connect(self._update_agent_status)

        if os.environ.get("SMART_LOCK_AUTO_START") == "1":
            QtCore.QTimer.singleShot(500, self.start)

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.video = QtWidgets.QLabel("待机中：仅红外检测，摄像头未开启")
        self.video.setMinimumSize(640, 480)
        self.video.setAlignment(QtCore.Qt.AlignCenter)
        self.video.setStyleSheet("background:#111; color:#ccc; border:1px solid #333;")
        layout.addWidget(self.video, 3)

        panel = QtWidgets.QWidget()
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setSpacing(9)
        layout.addWidget(panel, 1)

        self.step_label = QtWidgets.QLabel("当前步骤：待机，仅红外检测")
        self.step_label.setWordWrap(True)
        self.step_label.setMinimumHeight(48)
        self.step_label.setStyleSheet("font-size:18px; font-weight:600; color:#111;")
        panel_layout.addWidget(self.step_label)

        self.sensor_label = QtWidgets.QLabel("1. 红外检测：等待")
        self.face_label = QtWidgets.QLabel("2. 人脸识别：待机，红外触发后开启")
        self.live_label = QtWidgets.QLabel("3. 活体检测：待机")
        self.speaker_label = QtWidgets.QLabel("4. 声纹识别：待机")
        self.score_label = QtWidgets.QLabel("5. 融合结果：等待")
        for label in (self.sensor_label, self.face_label, self.live_label, self.speaker_label, self.score_label):
            label.setWordWrap(True)
            label.setMinimumHeight(32)
            panel_layout.addWidget(label)

        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("输入注册姓名")
        panel_layout.addWidget(self.name_input)

        enroll_buttons = QtWidgets.QHBoxLayout()
        self.capture_face_btn = QtWidgets.QPushButton("采集人脸")
        self.train_face_btn = QtWidgets.QPushButton("刷新人脸库")
        self.enroll_voice_btn = QtWidgets.QPushButton("采集声纹")
        enroll_buttons.addWidget(self.capture_face_btn)
        enroll_buttons.addWidget(self.train_face_btn)
        enroll_buttons.addWidget(self.enroll_voice_btn)
        panel_layout.addLayout(enroll_buttons)

        self.people_label = QtWidgets.QLabel("已注册：读取中")
        self.people_label.setWordWrap(True)
        panel_layout.addWidget(self.people_label)

        self.agent_status_label = QtWidgets.QLabel("语音 Agent：未启动")
        self.agent_status_label.setWordWrap(True)
        panel_layout.addWidget(self.agent_status_label)

        buttons = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("启动")
        self.stop_btn = QtWidgets.QPushButton("停止")
        self.auth_btn = QtWidgets.QPushButton("手动认证")
        self.stop_btn.setEnabled(False)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.auth_btn)
        panel_layout.addLayout(buttons)

        self.auto_auth = QtWidgets.QCheckBox("自动认证：红外触发后开启摄像头")
        self.auto_auth.setChecked(self._config.auto_auth.enabled)
        panel_layout.addWidget(self.auto_auth)

        self.voice_enabled = QtWidgets.QCheckBox("语音引导")
        self.voice_enabled.setChecked(self._config.voice_feedback.enabled)
        panel_layout.addWidget(self.voice_enabled)

        self.allow_unlock = QtWidgets.QCheckBox("允许真实开锁")
        self.allow_unlock.setChecked(False)
        panel_layout.addWidget(self.allow_unlock)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        panel_layout.addWidget(self.log, 1)

        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.auth_btn.clicked.connect(self.authenticate_once)
        self.capture_face_btn.clicked.connect(self.capture_face_sample)
        self.train_face_btn.clicked.connect(self.train_face_model)
        self.enroll_voice_btn.clicked.connect(self.enroll_voice_sample)
        self.setCentralWidget(root)
        self.refresh_people()

    def start(self) -> None:
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_step("当前步骤：正在预加载模型，请稍候")
        self._append("预加载人脸、活体、声纹模型；此过程只在启动时执行一次")
        self._preload_models()
        self._wait_for_agent_preload()
        self._set_step("当前步骤：待机，仅红外检测")
        self._append("启动红外传感器轮询；摄像头将在红外检测到靠近后开启")
        self._sensor_timer.start(max(100, int(self._config.sensor.poll_interval_seconds * 1000)))
        if not self._config.auto_auth.camera_triggered_by_sensor:
            self._ensure_camera_active("配置为摄像头常开模式")
        self._speak("app_started")
        self._agent_status_timer.start(1000)
        self._update_agent_status()

    def stop(self) -> None:
        self._set_step("当前步骤：已停止")
        self._append("已停止")
        self._sensor_timer.stop()
        self._stop_camera("停止程序")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _update_sensor(self) -> None:
        try:
            now = time.monotonic()
            raw_detected = self._sensor.detected()
            if raw_detected:
                self._sensor_true_until = now + self._config.sensor.presence_hold_seconds
            latched = now < self._sensor_true_until

            self._previous_sensor = self._last_sensor
            self._last_sensor = latched
            if latched:
                self.sensor_label.setText("1. 红外检测：通过，检测到人体靠近")
                self._on_presence_detected(rising_edge=not self._previous_sensor)
            else:
                self.sensor_label.setText("1. 红外检测：等待，未检测到人体")
                now = time.monotonic()
                if self._presence_lost_at is None:
                    self._presence_lost_at = now
                elif now - self._presence_lost_at >= self._config.auto_auth.absence_rearm_seconds:
                    self._auto_auth_paused_until = 0.0
                    self._unknown_face_since = None
                    self._event_reported_for_presence = False
                    self._last_event_report_attempt_at = 0.0
                self._maybe_enter_standby()
        except Exception as exc:
            self.sensor_label.setText(f"1. 红外检测：异常，{exc}")
            self._append(f"红外检测异常：{exc}")

    def _update_camera(self) -> None:
        try:
            frame = self._camera.read()
            self._last_frame = frame
            face = self._face_backend()
            result = face.verify(frame, self._config.system.dry_run)
            now = time.monotonic()
            if result.passed:
                self._face_pass_until = now + self._config.system.result_hold_seconds
            elif now < self._face_pass_until:
                result = AuthResult(
                    "face", True, result.score, "pass state held", result.metadata
                )
            self._last_face = result
            self.face_label.setText(self._format_result("2. 人脸识别", self._last_face))
            self._show_frame(self._draw_face(frame.copy(), self._last_face))
            self._maybe_report_unknown_face(result, now)
            self._maybe_auto_authenticate()
        except Exception as exc:
            self.video.setText(f"摄像头异常：{exc}")
            self._append(f"摄像头异常：{exc}")

    def _on_presence_detected(self, rising_edge: bool) -> None:
        now = time.monotonic()
        self._last_presence_at = now
        self._presence_lost_at = None
        if rising_edge:
            self._unknown_face_since = None
            self._event_reported_for_presence = False
            self._last_event_report_attempt_at = 0.0
            self._append("红外检测到有人靠近，开启摄像头和人脸识别")
            self._ensure_camera_active("红外检测到有人靠近")
            self._speak("presence", force=True)
            LOGGER.info("Presence rising edge; voice prompt triggered")
        if not self._auth_in_progress:
            self._set_step("当前步骤：红外已触发，正在做人脸识别")

    def _maybe_enter_standby(self) -> None:
        if not self._camera_active or self._auth_in_progress:
            return
        if not self._config.auto_auth.camera_triggered_by_sensor:
            return
        idle = time.monotonic() - self._last_presence_at
        if idle >= self._config.auto_auth.camera_idle_close_seconds:
            self._request_agent_reset()
            self._stop_camera(f"无人靠近超过 {idle:.1f} 秒")
            self._set_step("当前步骤：回到待机，仅红外检测")
            self.face_label.setText("2. 人脸识别：待机，红外触发后开启")
            self.live_label.setText("3. 活体检测：待机")
            self.speaker_label.setText("4. 声纹识别：待机")
            self.video.setText("待机中：仅红外检测，摄像头未开启")

    def _request_agent_reset(self) -> None:
        base = Path(self._config.agent.safety.auth_context_path).parent
        flag = base / "agent_reset.flag"
        if not flag.exists():
            flag.write_text("absence-idle", encoding="utf-8")
            LOGGER.info("Long absence; agent reset requested")
        try:
            (base / "auth_context.json").unlink(missing_ok=True)
        except OSError:
            pass

    def _ensure_camera_active(self, reason: str) -> None:
        if self._camera_active:
            return
        self._camera_active = True
        self._last_frame = None
        self._last_face = AuthResult("face", False, 0.0, "not checked")
        self.video.setText("正在开启摄像头...")
        self.face_label.setText("2. 人脸识别：摄像头开启中")
        self._append(f"开启摄像头：{reason}")
        self._camera_timer.start(120)

    def _stop_camera(self, reason: str) -> None:
        if not self._camera_active and self._last_frame is None:
            return
        self._camera_timer.stop()
        self._camera.close()
        self._camera_active = False
        self._last_frame = None
        self._last_face = AuthResult("face", False, 0.0, "not checked")
        self._append(f"关闭摄像头：{reason}")

    def _maybe_auto_authenticate(self) -> None:
        if not self.auto_auth.isChecked() or self._auth_in_progress:
            return
        if time.monotonic() < self._auto_auth_paused_until:
            return
        if self._config.auto_auth.require_sensor and not self._last_sensor:
            return
        if self._config.auto_auth.require_face and not self._last_face.passed:
            return

        now = time.monotonic()
        if now - self._last_auto_auth_at < self._config.auto_auth.cooldown_seconds:
            return

        self._last_auto_auth_at = now
        identity = self._last_face.metadata.get("identity") or "未知"
        self._set_step(f"当前步骤：红外和人脸已通过，自动认证 {identity}")
        self._append(f"自动认证触发：红外通过，人脸通过，身份={identity}")
        self._speak("auto_start")
        QtCore.QTimer.singleShot(self._config.auto_auth.auto_start_delay_ms, self.authenticate_once)

    def _maybe_report_unknown_face(self, face_result: AuthResult, now: float) -> None:
        if not self._last_sensor or face_result.passed or self._event_reported_for_presence:
            self._unknown_face_since = None
            return
        if self._unknown_face_since is None:
            self._unknown_face_since = now
            return
        if now - self._unknown_face_since < self._config.event_reporting.unknown_face_delay_seconds:
            return
        sensor_result = AuthResult("sensor", True, 1.0, "presence detected")
        decision = self._fusion.decide([sensor_result, face_result])
        self._report_auth_failure_once(decision.score, [sensor_result, face_result])

    def _report_auth_failure_once(self, score: float, results: list[AuthResult]) -> None:
        if self._event_reported_for_presence:
            return
        now = time.monotonic()
        if now - self._last_event_report_attempt_at < 5.0:
            return
        self._last_event_report_attempt_at = now
        if report_auth_failure(self._config.event_reporting, score, results):
            self._event_reported_for_presence = True

    def capture_face_sample(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            self._append("人脸采集失败：请先输入姓名")
            return
        self._ensure_camera_active("手动采集人脸")
        if self._last_frame is None:
            self._update_camera()
        if self._last_frame is None:
            self._append("人脸采集失败：没有摄像头画面")
            return
        face = self._face_backend()
        if not hasattr(face, "enroll_sample"):
            self._append("人脸采集失败：当前后端不支持注册")
            return
        try:
            save_path = face.enroll_sample(name, self._last_frame)
            sample_count = face.sample_count(name) if hasattr(face, "sample_count") else 0
            self._append(f"已保存第 {sample_count} 张人脸样本：{save_path}")
            self.refresh_people()
        except Exception as exc:
            self._append(f"人脸采集失败：{self._translate_reason(str(exc))}")

    def train_face_model(self) -> None:
        face = self._face_backend()
        if not hasattr(face, "train"):
            self._append("刷新失败：当前人脸后端不支持")
            return
        try:
            count = face.train()
            self._append(f"人脸库已刷新，共 {count} 个样本")
            self.refresh_people()
        except Exception as exc:
            self._append(f"人脸库刷新失败：{self._translate_reason(str(exc))}")

    def enroll_voice_sample(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            self._append("声纹采集失败：请先输入姓名")
            return
        speaker = self._speaker_backend()
        if not hasattr(speaker, "enroll_microphone"):
            self._append("声纹采集失败：当前后端不支持麦克风注册")
            return
        try:
            self._set_step("当前步骤：正在采集声纹，请自然说一句话")
            self._append(f"正在录制声纹，时长 {self._config.speaker.record_seconds:.1f} 秒；请自然说一句话")
            self._speak("voice_prompt", force=True, block=True)
            QtWidgets.QApplication.processEvents()
            save_path = speaker.enroll_microphone(name)
            sample_count = speaker.sample_count(name) if hasattr(speaker, "sample_count") else 0
            self._append(f"已保存第 {sample_count} 条声纹样本：{save_path}")
            self._set_step("当前步骤：声纹采集完成")
            self.refresh_people()
        except Exception as exc:
            self._append(f"声纹采集失败：{self._translate_reason(str(exc))}")
            self._set_step("当前步骤：声纹采集失败")

    def refresh_people(self) -> None:
        people = sorted(self._face_people_from_disk() | self._voice_people_from_disk())
        if not people:
            self.people_label.setText("已注册：暂无")
            return
        details = []
        for name in people:
            face_count = self._count_files(Path(self._config.face.embedding_dir) / name, {".npy", ".json"})
            if face_count == 0:
                face_count = self._count_files(Path(self._config.face.data_dir) / name, {".png", ".jpg", ".jpeg"})
            voice_count = self._count_files(Path(self._config.speaker.voice_dir) / name, {".npy", ".json"})
            details.append(f"{name} 人脸:{face_count} 声纹:{voice_count}")
        self.people_label.setText("已注册：" + "；".join(details))

    def authenticate_once(self) -> None:
        if self._auth_in_progress:
            self._append("认证正在进行中，请等待本次流程结束")
            return
        self._auth_in_progress = True
        self.auth_btn.setEnabled(False)
        started = time.monotonic()

        try:
            self._ensure_camera_active("开始认证")
            if self._last_frame is None:
                self._update_camera()
            if self._last_frame is None:
                self._append("认证失败：没有摄像头画面")
                return

            self._set_step("当前步骤：1/5 红外检测")
            sensor_result = AuthResult(
                "sensor",
                self._last_sensor,
                1.0 if self._last_sensor else 0.0,
                "presence detected" if self._last_sensor else "no presence detected",
            )
            self.sensor_label.setText(self._format_result("1. 红外检测", sensor_result))
            QtWidgets.QApplication.processEvents()

            self._set_step("当前步骤：2/5 人脸识别，请正对摄像头")
            face = self._face_backend()
            face_result = face.verify(self._last_frame, self._config.system.dry_run)
            self.face_label.setText(self._format_result("2. 人脸识别", face_result))
            self._speak("face_pass" if face_result.passed else "face_fail")
            QtWidgets.QApplication.processEvents()

            self._set_step("当前步骤：3/5 活体检测，请眨眼并左右转头")
            self.live_label.setText("3. 活体检测：进行中，请眨眼并左右转头")
            self._speak("liveness_prompt", force=True, block=True)
            live_result = self._run_motion_liveness()
            self.live_label.setText(self._format_result("3. 活体检测", live_result))
            self._speak("liveness_pass" if live_result.passed else "liveness_fail")
            QtWidgets.QApplication.processEvents()

            self._set_step("当前步骤：4/5 声纹识别，请自然说一句话")
            self.speaker_label.setText("4. 声纹识别：录音中，请自然说一句话")
            self._append(f"正在录制认证语音，时长 {self._config.speaker.record_seconds:.1f} 秒；请自然说一句话")
            self._speak("voice_prompt", force=True, block=True)
            QtWidgets.QApplication.processEvents()
            speaker_result = self._verify_voice()
            speaker_result = self._apply_identity_consistency(face_result, speaker_result)
            self.speaker_label.setText(self._format_result("4. 声纹识别", speaker_result))
            self._speak("voice_pass" if speaker_result.passed else "voice_fail")
            if live_result.passed:
                self._liveness_pass_until = time.monotonic() + self._config.system.result_hold_seconds
            if speaker_result.passed:
                self._speaker_pass_until = time.monotonic() + self._config.system.result_hold_seconds

            decision = self._fusion.decide([sensor_result, face_result, live_result, speaker_result])
            self._write_decision_context(decision)
            self._set_step("当前步骤：5/5 多模态融合判定")
            self.score_label.setText(f"5. 融合结果：{decision.score:.3f} / 阈值 {self._config.fusion.threshold:.2f}")

            elapsed = time.monotonic() - started
            if decision.passed:
                self._on_auth_passed(elapsed)
            else:
                self._on_auth_failed(decision.score, sensor_result, face_result, live_result, speaker_result)
        finally:
            self._auth_in_progress = False
            if self._last_auth_passed:
                self._last_auto_auth_at = time.monotonic()
            else:
                self._last_auto_auth_at = (
                    time.monotonic()
                    - self._config.auto_auth.cooldown_seconds
                    + self._config.auto_auth.failed_retry_seconds
                )
            self.auth_btn.setEnabled(True)
            self._maybe_enter_standby()

    def _write_decision_context(self, decision) -> None:
        path = self._config.agent.safety.auth_context_path
        if self._config.lock.flow == "agent_confirm" and not decision.passed:
            existing = read_auth_context(path)
            if existing and existing.get("fusion_passed") and not existing.get("consumed"):
                LOGGER.info("Keeping existing valid credential; ignoring failed retry decision")
                return
        write_auth_context(path, decision)

    def _verify_voice(self) -> AuthResult:
        speaker = self._speaker_backend()
        if hasattr(speaker, "verify_microphone"):
            return speaker.verify_microphone()
        return speaker.verify(self._config.system.dry_run)

    @staticmethod
    def _apply_identity_consistency(face_result: AuthResult, speaker_result: AuthResult) -> AuthResult:
        face_identity = face_result.metadata.get("identity")
        speaker_identity = speaker_result.metadata.get("identity")
        if (
            face_result.passed
            and speaker_result.passed
            and face_identity
            and speaker_identity
            and face_identity != speaker_identity
        ):
            metadata = dict(speaker_result.metadata)
            metadata["face_identity"] = face_identity
            return AuthResult("speaker", False, speaker_result.score, "voice identity does not match face", metadata)
        return speaker_result

    def _on_auth_passed(self, elapsed: float) -> None:
        self._last_auth_passed = True
        self._speak("auth_pass", force=True)
        if self._config.lock.flow == "agent_confirm":
            self._set_step("认证通过：请说出开门指令")
            self._append(f"认证通过，用时 {elapsed:.2f} 秒；等待语音 Agent 开门指令")
            # Keep the microphone free for the voice Agent until the person leaves.
            self._auto_auth_paused_until = float("inf")
            return
        if self.allow_unlock.isChecked():
            self._lock.unlock()
            self._set_step("认证通过：已发送开锁信号")
            self._append(f"认证通过，用时 {elapsed:.2f} 秒；已发送继电器开锁信号")
        else:
            self._set_step("认证通过：调试模式已阻止真实开锁")
            self._append(f"认证通过，用时 {elapsed:.2f} 秒；界面未允许真实开锁")

    def _on_auth_failed(
        self,
        score: float,
        sensor_result: AuthResult,
        face_result: AuthResult,
        live_result: AuthResult,
        speaker_result: AuthResult,
    ) -> None:
        self._last_auth_passed = False
        self._speak("auth_fail", force=True)
        self._set_step("认证失败：请查看红外、人脸、活体、声纹状态")
        self._append(
            "认证失败："
            f"融合分数={score:.3f}，"
            f"红外={self._cn_pass(sensor_result.passed)}，"
            f"人脸={self._cn_pass(face_result.passed)}，"
            f"活体={self._cn_pass(live_result.passed)}，"
            f"声纹={self._cn_pass(speaker_result.passed)}"
        )
        self._report_auth_failure_once(
            score, [sensor_result, face_result, live_result, speaker_result]
        )

    def _run_motion_liveness(self) -> AuthResult:
        if self._config.system.dry_run and self._config.liveness.pass_in_dry_run:
            return AuthResult("liveness", True, 1.0, "dry-run liveness accepted")

        self._append("活体检测开始：请眨眼一次，并明显左右转头")
        QtWidgets.QApplication.processEvents()
        frames: list[np.ndarray] = []
        centers: list[float] = []
        deadline = time.monotonic() + self._config.liveness.duration_seconds
        while time.monotonic() < deadline:
            try:
                frame = self._camera.read()
            except Exception as exc:
                return AuthResult("liveness", False, 0.0, f"camera failed: {exc}")
            self._last_frame = frame
            face = self._face_backend()
            bbox = None
            if hasattr(face, "detect_largest_face"):
                bbox, _ = face.detect_largest_face(frame)
                if bbox is not None:
                    x, _y, w, _h = bbox
                    centers.append((x + w / 2.0) / frame.shape[1])
                    tracking = AuthResult("face", True, 1.0, "tracking", {"bbox": bbox})
                    self._show_frame(self._draw_face(frame.copy(), tracking))
            frames.append(self._crop_face_region(frame, bbox).copy() if bbox is not None else frame.copy())
            QtWidgets.QApplication.processEvents()
            time.sleep(0.08)

        liveness = self._liveness_backend()
        if hasattr(liveness, "verify_frames"):
            live_result = liveness.verify_frames(frames)
            if live_result.passed or not centers:
                return live_result
            fallback = self._fallback_motion_liveness(centers)
            if fallback.score > live_result.score:
                return fallback
            return live_result
        return self._fallback_motion_liveness(centers)

    @staticmethod
    def _crop_face_region(frame: np.ndarray, bbox: tuple[int, int, int, int], margin: float = 0.35) -> np.ndarray:
        x, y, w, h = bbox
        mx = int(w * margin)
        my = int(h * margin)
        x1 = max(0, x - mx)
        y1 = max(0, y - my)
        x2 = min(frame.shape[1], x + w + mx)
        y2 = min(frame.shape[0], y + h + my)
        if x2 - x1 < 80 or y2 - y1 < 80:
            return frame
        return frame[y1:y2, x1:x2]

    def _fallback_motion_liveness(self, centers: list[float]) -> AuthResult:
        if len(centers) < 5:
            return AuthResult("liveness", False, 0.0, "face tracking failed")
        motion = max(centers) - min(centers)
        score = min(1.0, motion / self._config.liveness.min_motion_ratio)
        passed = score >= self._config.liveness.min_score
        reason = f"motion={motion:.3f}" if passed else f"motion too small ({motion:.3f})"
        return AuthResult("liveness", passed, float(score), reason, {"motion": motion})

    def _wait_for_agent_preload(self, timeout_seconds: float = 180.0) -> None:
        path = Path(self._config.agent.safety.auth_context_path).parent / "agent_status.txt"
        deadline = time.monotonic() + timeout_seconds
        self._set_step("当前步骤：等待语音 Agent 模型加载")
        self._append("等待语音 Agent 模型加载，请稍候...")
        while time.monotonic() < deadline:
            try:
                status = path.read_text(encoding="utf-8").strip()
            except OSError:
                status = ""
            if any(key in status for key in ("等待硬件认证", "语音对话运行中", "认证通过")):
                LOGGER.info("Voice Agent ready: %s", status)
                return
            QtWidgets.QApplication.processEvents()
            time.sleep(0.5)
        LOGGER.warning("Timed out waiting for voice Agent preload; continuing anyway")

    def _preload_models(self) -> None:
        if self._models_preloaded:
            return
        LOGGER.info("GUI model preload started")
        steps = (
            ("人脸识别模型", self._face_backend, "2. 人脸识别"),
            ("活体检测模型", self._liveness_backend, "3. 活体检测"),
            ("声纹识别模型", self._speaker_backend, "4. 声纹识别"),
        )
        for index, (name, loader, label) in enumerate(steps, 1):
            self._set_step(f"当前步骤：正在预加载 {name}（{index}/3）")
            self._append(f"预加载 {name} ...")
            QtWidgets.QApplication.processEvents()
            try:
                backend = loader()
                if name == "声纹识别模型" and hasattr(backend, "ensure_model"):
                    backend.ensure_model()
            except Exception as exc:
                self._append(f"预加载 {name} 失败，将在使用时重试：{self._translate_reason(str(exc))}")
        self._models_preloaded = True
        self._set_step("当前步骤：模型预加载完成")
        self._append("模型预加载完成")
        LOGGER.info("GUI model preload finished")

    def _face_backend(self):
        if self._face is None:
            self._set_step("当前步骤：正在加载人脸识别模型")
            self._append("加载 InsightFace 人脸识别后端")
            QtWidgets.QApplication.processEvents()
            self._face = create_face_authenticator(self._config.face)
        return self._face

    def _liveness_backend(self):
        if self._liveness is None:
            self._set_step("当前步骤：正在加载活体检测模型")
            self._append("加载 MediaPipe 活体检测后端")
            QtWidgets.QApplication.processEvents()
            self._liveness = create_liveness_checker(self._config.liveness)
        return self._liveness

    def _speaker_backend(self):
        if self._speaker is None:
            self._set_step("当前步骤：正在加载声纹识别模型")
            self._append("加载 SpeechBrain 声纹识别后端")
            QtWidgets.QApplication.processEvents()
            self._speaker = create_speaker_authenticator(self._config.speaker)
        return self._speaker

    def _update_agent_status(self) -> None:
        path = Path(self._config.agent.safety.auth_context_path).parent / "agent_status.txt"
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if not text:
            text = "未检测到 Agent 状态文件"
        if text != self._last_agent_status:
            self._last_agent_status = text
            self.agent_status_label.setText(f"语音 Agent：{text}")
            self._append(f"语音 Agent 状态：{text}")
            LOGGER.info("Agent status: %s", text)
            self._agent_running = "运行中" in text

    def _show_frame(self, frame: np.ndarray) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QtGui.QImage(rgb.data, w, h, ch * w, QtGui.QImage.Format_RGB888).copy()
        pixmap = QtGui.QPixmap.fromImage(image)
        self.video.setPixmap(pixmap.scaled(self.video.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))

    @staticmethod
    def _draw_face(frame: np.ndarray, result: AuthResult) -> np.ndarray:
        bbox = result.metadata.get("bbox")
        if isinstance(bbox, tuple) and len(bbox) == 4:
            x, y, w, h = bbox
            color = (0, 220, 0) if result.passed else (0, 180, 255)
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            cv2.putText(frame, f"face {result.score:.2f}", (x, max(20, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return frame

    def _append(self, message: str) -> None:
        self.log.appendPlainText(time.strftime("%H:%M:%S ") + message)

    def _set_step(self, message: str) -> None:
        self.step_label.setText(message)

    def _speak(self, key: str, text: str | None = None, force: bool = False, block: bool | None = None) -> None:
        if self._agent_running:
            LOGGER.info("Agent is running; GUI voice prompt suppressed: %s", key)
            return
        if self.voice_enabled.isChecked():
            self._voice.speak(key, text=text, force=force, block=block)

    def _format_result(self, title: str, result: AuthResult) -> str:
        status = self._cn_pass(result.passed)
        reason = self._translate_reason(result.reason)
        identity = result.metadata.get("identity")
        identity_text = f"，身份：{identity}" if identity else ""
        return f"{title}：{status}，分数 {result.score:.2f}{identity_text}，{reason}"

    @staticmethod
    def _face_people_from_disk() -> set[str]:
        return SmartLockWindow._people_dirs([Path("database/face_embeddings"), Path("database/faces")])

    def _voice_people_from_disk(self) -> set[str]:
        return self._people_dirs([Path(self._config.speaker.voice_dir)])

    @staticmethod
    def _people_dirs(roots: list[Path]) -> set[str]:
        people: set[str] = set()
        for root in roots:
            if root.exists():
                people.update(p.name for p in root.iterdir() if p.is_dir())
        return people

    @staticmethod
    def _count_files(root: Path, suffixes: set[str]) -> int:
        if not root.exists():
            return 0
        return sum(1 for path in root.iterdir() if path.is_file() and path.suffix.lower() in suffixes)

    @staticmethod
    def _cn_pass(passed: bool) -> str:
        return "通过" if passed else "失败"

    @staticmethod
    def _translate_reason(reason: Optional[str]) -> str:
        if not reason:
            return "等待检测"
        text = str(reason)
        replacements = {
            "not checked": "尚未检测",
            "no face detected": "未检测到人脸",
            "recognized": "识别到",
            "unknown or low confidence": "未知人员或置信度不足",
            "face detected but model not trained": "检测到人脸，但人脸库为空",
            "InsightFace model ready but face library is empty": "InsightFace 已就绪，但人脸库为空",
            "voice model not enrolled": "声纹库为空，请先采集声纹",
            "not enough voice audio": "有效语音时长不足，请自然说几句话",
            "voice identity does not match face": "声纹身份与人脸身份不一致",
            "dry-run liveness accepted": "模拟模式通过",
            "motion liveness requires GUI": "需要在界面中采集连续画面",
            "liveness needs frame sequence": "需要连续画面进行活体检测",
            "no frames": "没有采集到画面",
            "no face landmarks": "未检测到面部关键点",
            "not enough face landmarks": "面部关键点不足",
            "face tracking failed": "人脸跟踪失败",
            "motion too small": "动作幅度太小",
            "camera failed": "摄像头失败",
            "dry-run mock face accepted": "模拟人脸通过",
            "dry-run face accepted": "模拟模式人脸通过",
            "dry-run speaker accepted": "模拟模式声纹通过",
            "presence detected": "检测到人体靠近",
            "no presence detected": "未检测到人体靠近",
            "tracking": "跟踪中",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.stop()
        self._sensor.close()
        self._lock.close()
        if self._liveness is not None and hasattr(self._liveness, "close"):
            self._liveness.close()
        event.accept()


def main() -> None:
    parser = argparse.ArgumentParser(description="PySide6 smart lock MVP GUI")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="force desktop/demo mode")
    mode.add_argument("--hardware", action="store_true", help="force Jetson hardware mode")
    parser.add_argument("--no-unlock", action="store_true", help="never drive the relay output")
    args = parser.parse_args()

    config = load_config("config.yaml")
    if args.dry_run:
        config = replace(config, system=replace(config.system, dry_run=True))
    elif args.hardware:
        config = replace(config, system=replace(config.system, dry_run=False))

    setup_logging(config.system.log_level)
    app = QtWidgets.QApplication(sys.argv)
    window = SmartLockWindow(config, force_lock_dry_run=args.no_unlock)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
