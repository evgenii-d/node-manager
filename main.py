''' NodeManager '''
import sys
import time
import socket
import threading
import concurrent.futures
import requests
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QSize, Signal, Qt, QThread, QObject
from PySide6.QtWidgets import (QApplication, QWidget, QMainWindow,
                               QVBoxLayout, QScrollArea, QHBoxLayout,
                               QLabel, QCheckBox, QGroupBox, QToolBar, QPushButton)


class Worker(QObject):
    ''' Worker Thread '''
    scanPort = 5000
    activeNodes = Signal(list)

    def checkIP(self, address: str, port: int, timeout: int) -> bool | tuple:
        ''' Check IP address. Return address and port if reachable '''
        try:
            socket.setdefaulttimeout(timeout)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((address, port))
        except OSError:
            return False
        sock.close()
        return {'address': address, 'port': port}

    def localIP(self) -> str:
        ''' Get local IP address '''
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(('10.0.0.0', 0))
        return sock.getsockname()[0]

    def scanLAN(self, port: int, prefix: str = '192.168.1', timeout: int = 1) -> list:
        ''' Scan local network for active nodes '''
        result = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            tasks = [executor.submit(
                self.checkIP, f'{prefix}.{i}', port, timeout) for i in range(1, 255)]

            for task in concurrent.futures.as_completed(tasks):
                if task.result():
                    result.append(task.result())
        return result

    def scanNodes(self) -> None | list:
        ''' Signal for scan worker '''
        networkPrefix = '.'.join(self.localIP().split('.')[0:3])
        nodes = self.scanLAN(self.scanPort, networkPrefix)
        if nodes:
            self.activeNodes.emit(nodes)
        else:
            self.activeNodes.emit(None)


class MainWindow(QMainWindow):
    ''' Node Manager '''
    nodes = []
    groupBoxes = []
    toolbar = None
    checkNameInterval = 60
    contentLayout = QVBoxLayout()
    updateListSignal = Signal(dict)
    updateNodeNameSignal = Signal(dict)
    workerSignal = Signal()

    def __init__(self):
        super().__init__()

        self.setWindowTitle('Node Manager')
        self.setMinimumSize(QSize(320, 240))
        self.resize(640, 480)
        self.setWindowIcon(QIcon('icon.ico'))

        scrollArea = QScrollArea()
        scrollArea.setWidgetResizable(True)

        self.toolbar = QToolBar('Toolbar')
        self.toolbar.setMovable(False)
        self.toolbar.toggleViewAction().setEnabled(False)
        self.addToolBar(self.toolbar)

        toogleAction = QAction('Toggle Selection', self)
        toogleAction.setCheckable(True)
        toogleAction.triggered.connect(self.toggleSelection)

        clearAction = QAction('Clear List', self)
        clearAction.triggered.connect(self.clearList)

        rebootAction = QAction('Reboot', self)
        rebootAction.setToolTip('Reboot selected nodes')
        rebootAction.triggered.connect(self.rebootHandler)

        shutdownAction = QAction('Shutdown', self)
        shutdownAction.setToolTip('Shutdown selected nodes')
        shutdownAction.triggered.connect(self.shutdownHandler)

        scanAction = QAction('Scan', self)
        scanAction.setToolTip('Scan LAN for active Nodes')
        scanAction.triggered.connect(self.scanHandler)

        self.toolbar.addActions(
            (toogleAction, clearAction, rebootAction, shutdownAction, scanAction))

        widget = QWidget()
        widget.setLayout(self.contentLayout)
        scrollArea.setWidget(widget)
        self.setCentralWidget(scrollArea)

        self.updateListSignal.connect(self.addGroupBox)
        self.updateNodeNameSignal.connect(self.updateNodeName)

        self.worker = Worker()
        self.workerThread = QThread()
        self.worker.activeNodes.connect(self.addNodes)
        self.workerSignal.connect(self.worker.scanNodes)
        self.worker.moveToThread(self.workerThread)
        self.workerThread.start()

        t = threading.Thread(target=self.checkNodeName)
        t.daemon = True
        t.start()

    def addNodes(self, nodes):
        ''' Add node to layout '''
        for node in nodes:
            if node not in self.nodes:
                self.nodes.append(node)
                self.updateListSignal.emit(node)
        self.setDisabled(False)

    def makeGroupBox(self, **node) -> QGroupBox:
        ''' Make new GroupBox '''
        url = f"http://{node['address']}:{node['port']}"

        groupbox = QGroupBox()
        groupbox.setProperty('address', node['address'])
        groupbox.setProperty('port', node['port'])

        layout = QHBoxLayout()
        checkbox = QCheckBox()

        addressLabel = QLabel()
        addressLabel.setText(f'<a href="{url}">{node["address"]}</a>')
        addressLabel.setOpenExternalLinks(True)

        nameLabel = QLabel()
        nameLabel.setText('—')
        nameLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        removeButton = QPushButton('Remove')
        removeButton.setToolTip('Remove from list')
        removeButton.clicked.connect(lambda _: self.removeGroupBox(groupbox))

        layout.addWidget(checkbox)
        layout.addWidget(addressLabel)
        layout.addWidget(nameLabel)
        layout.addWidget(removeButton)
        groupbox.setLayout(layout)
        return groupbox

    def checkNodeName(self):
        ''' Check node name '''
        while True:
            time.sleep(self.checkNameInterval)
            for group in self.groupBoxes:
                try:
                    address = group.property('address')
                    port = group.property('port')
                    api = f"http://{address}:{port}/node-details/name"
                    nameLabel = group.children()[3]
                except RuntimeError:
                    continue

                try:
                    name = requests.get(api, timeout=1).json()
                    data = {'label': nameLabel, 'name': name}
                    self.updateNodeNameSignal.emit(data)
                except requests.exceptions.RequestException:
                    pass

    def addGroupBox(self, node: dict):
        ''' Add GroupBox to layout '''
        group = self.makeGroupBox(**node)
        self.groupBoxes.append(group)
        self.contentLayout.addWidget(group)

    def removeGroupBox(self, groupBox: QGroupBox):
        ''' Remove GroupBox to layout '''
        address = groupBox.property('address')

        self.groupBoxes.remove(groupBox)
        groupBox.deleteLater()
        for node in self.nodes:
            if node['address'] == address:
                self.nodes.remove(node)
                break

    def disableGroupBoxes(self, state: bool):
        ''' Disable all GroupBoxes '''
        for group in self.groupBoxes:
            group.setDisabled(state)

    def machineControl(self, command: str):
        ''' Control nodes through API '''
        if len(self.groupBoxes) == 0:
            return
        self.toolbar.setDisabled(True)
        self.disableGroupBoxes(True)

        for group in self.groupBoxes:
            address = group.property('address')
            port = group.property('port')
            api = f'http://{address}:{port}/machine-control'
            checkbox = group.children()[1]

            if checkbox.isChecked():
                group.setDisabled(True)
                try:
                    requests.post(api, json=command, timeout=5)
                    self.removeGroupBox(group)
                except requests.exceptions.RequestException:
                    pass

        self.disableGroupBoxes(False)
        self.toolbar.setDisabled(False)

    def rebootHandler(self):
        ''' Reboot selected node '''
        t = threading.Thread(target=self.machineControl,
                             args=['reboot'])
        t.daemon = True
        t.start()

    def shutdownHandler(self):
        ''' Shutdown selected node '''
        t = threading.Thread(target=self.machineControl,
                             args=['shutdown'])
        t.daemon = True
        t.start()

    def scanHandler(self):
        ''' Scan signal '''
        self.workerSignal.emit()
        self.setDisabled(True)

    def toggleSelection(self):
        ''' Toggle selection for all nodes in list '''
        state = self.sender().isChecked()
        for group in self.groupBoxes:
            checkbox = group.children()[1]
            checkbox.setChecked(state)

    def updateNodeName(self, data):
        ''' Update node name '''
        try:
            data['label'].setText(data['name'])
        except RuntimeError:
            pass

    def clearList(self):
        ''' Remove all nodes from layout '''
        self.toolbar.setDisabled(True)
        for groupbox in self.groupBoxes[:]:
            self.removeGroupBox(groupbox)
        self.toolbar.setDisabled(False)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.exec()
