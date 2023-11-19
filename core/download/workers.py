import os
import queue
import sys
import logging
import threading
from .download import *
from PyQt5.QtCore import Qt, QObject, QRunnable, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QStandardItem
from .helpers import is_valid_link
from .recapcha import *

# Create a lock to synchronize access to the proxy list
proxy_queue = queue.Queue()


class WorkerSignals(QObject):
    download_signal = pyqtSignal(list, str, bool, str, int)
    alert_signal = pyqtSignal(str)
    update_signal = pyqtSignal(list, list)
    unpause_signal = pyqtSignal(list, str, bool, str)


class FilterWorker(QRunnable):
    def __init__(self, actions, cached_download='', password=''):
        super(FilterWorker, self).__init__()
        self.links = actions.gui.links
        self.gui = actions.gui
        self.cached_downloads = actions.cached_downloads
        self.cached_download = cached_download
        self.signals = WorkerSignals()
        self.dl_name = cached_download[1] if self.cached_download else None
        self.password = cached_download[2] if self.cached_download else (
            password if password else None)
        self.progress = cached_download[3] if self.cached_download else None

    @pyqtSlot()
    def run(self):
        self.valid_links = []

        if isinstance(self.links, str):
            self.valid_links = [self.links]
        else:
            links = self.links.toPlainText().splitlines()
            for link in links:
                logging.debug('valid_links:'+str(link.strip()))
                # If the shortened URL is ouo bypass, recaptcha bypass
                if 'ouo.io' in link:
                    link = ouo_bypass(url=link)['bypassed_link']
                    logging.debug('bypassed link: ' + str(link))

                link = link.strip()
                if is_valid_link(link):
                    if not 'https://' in link[0:8] and not 'http://' in link[0:7]:
                        link = f'https://{link}'
                    if '&' in link:
                        link = link.split('&')[0]
                    self.valid_links.append(link)

            if not self.valid_links:
                self.signals.alert_signal.emit(
                    'The link(s) you inserted were not valid.')
                self.gui.hide_loading_overlay()
                # Reset link input window
                self.gui.add_btn.setEnabled(True)
                self.gui.links.setEnabled(True)
                self.gui.password.setEnabled(True)

        for link in self.valid_links:
            if '/dir/' in link:
                folder = requests.get(f'{link}?json=1')
                folder = folder.json()
                for f in folder:
                    link = f['link']
                    info = [f['filename'], convert_size(int(f['size']))]
                    info.extend(['Added', None, '0 B/s', ''])
                    row = []

                    for val in info:
                        data = QStandardItem(val)
                        data.setFlags(data.flags() & ~Qt.ItemIsEditable)
                        row.append(data)

                    if f['password'] == 1:
                        password = QStandardItem(self.password)
                        row.append(password)
                        self.gui.hide_loading_overlay()
                    else:
                        no_password = QStandardItem('No password')
                        no_password.setFlags(data.flags() & ~Qt.ItemIsEditable)
                        row.append(no_password)

                    self.signals.download_signal.emit(
                        row, link, True, self.dl_name, self.progress)
                    if self.cached_download:
                        self.cached_downloads.remove(self.cached_download)
            else:
                info = get_link_info(link)
                if info is not None:
                    is_private = True if info[0] == 'Private File' else False
                    info[0] = self.dl_name if self.dl_name else info[0]
                    info.extend(['Added', None, '0 B/s', ''])
                    row = []

                    for val in info:
                        data = QStandardItem(val)
                        data.setFlags(data.flags() & ~Qt.ItemIsEditable)
                        row.append(data)

                    if is_private:
                        password = QStandardItem(self.password)
                        row.append(password)
                        self.gui.hide_loading_overlay()
                    else:
                        no_password = QStandardItem('No password')
                        no_password.setFlags(data.flags() & ~Qt.ItemIsEditable)
                        row.append(no_password)

                    self.signals.download_signal.emit(
                        row, link, True, self.dl_name, self.progress)
                    if self.cached_download:
                        self.cached_downloads.remove(self.cached_download)


class DownloadWorker(QRunnable):
    def __init__(self, link, table_model, data, settings, dl_name=''):
        super(DownloadWorker, self).__init__()
        # Args
        self.link = link
        self.table_model = table_model
        self.data = data
        self.signals = WorkerSignals()
        self.paused = self.stopped = self.complete = False
        self.dl_name = dl_name

        # Default settings
        self.timeout = 30

        # Set user's download folder path
        user_home_directory = os.path.expanduser("~")
        self.dl_directory = os.path.join(user_home_directory, "Downloads")
        # Below is the program execution path
        # self.dl_directory = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(download_folder)))

        # Proxies Settings
        self.proxy_settings = None

        # Override defaults
        if settings:
            if settings[0]:
                self.dl_directory = settings[0]
            if settings[2]:
                self.timeout = settings[2]
            if settings[3]:
                self.proxy_settings = settings[3]

        if proxy_queue.qsize() == 0:
            # Load proxy only when queue is empty
            time.sleep(3)
            self.load_proxies()

        # Proxies
        self.proxies = proxy_queue

    def load_proxies(self):
        global proxy_queue
        proxies = get_proxies(settings=self.proxy_settings)
        for proxy in proxies:
            proxy_queue.put(proxy)

    # Run download task as thread
    def run(self):
        download_thread = threading.Thread(target=self.download)
        download_thread.start()

    @pyqtSlot()
    def run(self):
        dl_name = download(self)
        self.dl_name = dl_name

        if dl_name and self.stopped:
            logging.debug('Stop Download')
            if (dl_name):
                try:
                    os.remove(self.dl_directory + '/' + str(dl_name))
                    logging.debug(
                        f'Temp File Remove: {self.dl_directory}/{dl_name}')
                except:
                    logging.debug(
                        f'Failed to remove: {self.dl_directory}/{dl_name}')

        if not self.paused:
            logging.debug('Remove Download')
            if not dl_name:
                self.complete = True

    def stop(self, i):
        self.table_model.removeRow(i)
        self.stopped = True

    def pause(self):
        if not self.complete:
            self.paused = True

    def resume(self):
        if self.paused == True:
            self.paused = False
            if isinstance(self.data, list):
                self.signals.unpause_signal.emit(
                    self.data, self.link, False, self.dl_name)

    def return_data(self):
        if not self.stopped and not self.complete:
            data = []
            data.append(self.link)
            data.append(self.dl_name) if self.dl_name else data.append(None)
            data.append(self.data[6].text()) if self.data[6].text(
            ) != 'No password' else data.append(None)
            data.append(self.data[5].value())
            return data
