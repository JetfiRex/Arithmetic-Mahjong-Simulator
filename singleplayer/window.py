"""A single desktop window containing the local table and its loading screen."""

import logging
import os
from threading import Thread

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from singleplayer.desktop import DesktopSession, RESOURCE_ROOT


class _Signals(QObject):
    ready = Signal()
    failed = Signal(str)
    stopped = Signal()
    stop_failed = Signal(str)


class _TablePage(QWebEnginePage):
    """Keep navigation inside this application; never launch a system browser."""

    def __init__(self, profile, parent):
        super().__init__(profile, parent)
        self.origin = None
        self.script_errors = []

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.toString() == "about:blank" or (
            self.origin is not None and url.scheme() == self.origin.scheme()
            and url.host() == self.origin.host() and url.port() == self.origin.port())

    def javaScriptConsoleMessage(self, level, message, line, source):
        logging.getLogger(__name__).info("Table JavaScript: %s (%s:%s)", message, source, line)
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.script_errors.append(message)


class GameWindow(QMainWindow):
    def __init__(self, session=None):
        super().__init__()
        self.session = session if session is not None else DesktopSession()
        self.start_error = None
        self.starting = True
        self.closing = self.stopping = self.closed = False
        self.setWindowTitle("算术麻将 · 单机版")
        self.setWindowIcon(QIcon(str(RESOURCE_ROOT / "pictures/app_icon.ico")))
        available = self.screen().availableGeometry()
        self.resize(min(1440, int(available.width() * 0.95)), min(960, int(available.height() * 0.92)))
        self.setMinimumSize(800, 600)
        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)
        self.loading = QWidget(self)
        self.loading.setStyleSheet("background: #f3f5f3; color: #183d32;")
        layout = QVBoxLayout(self.loading)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("算术麻将", self.loading)
        title.setStyleSheet("font: bold 32px 'Microsoft YaHei UI';")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        self.status = QLabel("正在准备游戏和内置模型……", self.loading)
        self.status.setStyleSheet("font: 15px 'Microsoft YaHei UI'; padding: 16px;")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar(self.loading)
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(320)
        layout.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.exit_button = QPushButton("退出游戏", self.loading)
        self.exit_button.setStyleSheet("font: 15px 'Microsoft YaHei UI'; padding: 12px 28px;")
        self.exit_button.clicked.connect(self.close)
        self.exit_button.hide()
        layout.addWidget(self.exit_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.stack.addWidget(self.loading)

        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self)
        self.page = _TablePage(self.profile, self.view)
        self.view.setPage(self.page)
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        self.view.loadFinished.connect(self._loaded)
        self.stack.addWidget(self.view)
        self.events = _Signals(self)
        self.events.ready.connect(self._ready)
        self.events.failed.connect(self._failed)
        self.events.stopped.connect(self._stopped)
        self.events.stop_failed.connect(self._stop_failed)
        self.monitor = QTimer(self)
        self.monitor.timeout.connect(self._monitor)
        self.monitor.start(1000)
        Thread(target=self._initialize, name="game-window-startup", daemon=True).start()

    def _initialize(self):
        try:
            self.session.start()
            self.events.ready.emit()
        except Exception as exc:
            logging.exception("Desktop startup failed")
            self.events.failed.emit(str(exc))

    def _ready(self):
        self.starting = False
        if self.closing:
            self._begin_shutdown()
            return
        self.status.setText("正在打开游戏首页……")
        self.page.origin = QUrl(self.session.url)
        self.view.load(self.page.origin)

    def _loaded(self, success):
        if self.closing:
            return
        if success:
            self.stack.setCurrentWidget(self.view)
        else:
            self._failed("游戏页面加载失败，请重新打开游戏。")

    def _failed(self, message):
        self.starting = False
        self.start_error = message
        if self.closing:
            self._begin_shutdown()
            return
        self.status.setText(f"启动失败：{message}")
        self.progress.hide()
        self.exit_button.show()
        self.stack.setCurrentWidget(self.loading)

    def _monitor(self):
        if not self.starting and not self.closing and self.start_error is None:
            if self.session.thread and not self.session.thread.is_alive():
                self._failed("牌桌已停止，请重新打开游戏。")

    def closeEvent(self, event):
        if self.closed:
            event.accept()
            return
        event.ignore()
        self.closing = True
        self.view.stop()
        self.stack.setCurrentWidget(self.loading)
        self.status.setText("正在退出游戏……")
        self.progress.show()
        self.exit_button.hide()
        if not self.starting:
            self._begin_shutdown()

    def _begin_shutdown(self):
        if self.stopping:
            return
        self.stopping = True

        def stop():
            try:
                self.session.close()
                self.events.stopped.emit()
            except Exception as exc:
                logging.exception("Desktop shutdown failed")
                self.events.stop_failed.emit(str(exc))

        Thread(target=stop, name="game-window-shutdown", daemon=True).start()

    def _stop_failed(self, message):
        self.stopping = self.closing = False
        self.status.setText(f"退出尚未完成，请再试一次。\n{message}")
        self.progress.hide()
        self.exit_button.show()

    def _stopped(self):
        self.closed = True
        self.monitor.stop()
        self.close()


def application():
    # Let Qt use the system's graphics acceleration and automatic fallback.
    # Keep an explicit escape hatch for devices with broken graphics drivers.
    if os.environ.get("MAHJONG_SOFTWARE_RENDERING") == "1":
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " --disable-gpu"
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)
    app = QApplication([])
    app.setApplicationName("ArithmeticMahjong")
    app.setApplicationDisplayName("算术麻将 · 单机版")
    app.setOrganizationName("ArithmeticMahjong")
    app.setWindowIcon(QIcon(str(RESOURCE_ROOT / "pictures/app_icon.ico")))
    return app


def run_window():
    app = application()
    window = GameWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()
