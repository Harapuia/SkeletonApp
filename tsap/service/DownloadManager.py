# coding: utf-8
# Written by Wendo Sabée
# Manages local downloads

import threading
import binascii
import time
import os

# Setup logger
import logging
_logger = logging.getLogger(__name__)

from Tribler.Core.TorrentDef import TorrentDef
from Tribler.Core.simpledefs import DOWNLOAD, UPLOAD
from Tribler.Main.globals import DefaultDownloadStartupConfig

from Tribler.Core.Video.VideoPlayer import VideoPlayer


# Tribler defs
from Tribler.Core.simpledefs import NTFY_MISC, NTFY_TORRENTS, NTFY_MYPREFERENCES, \
    NTFY_VOTECAST, NTFY_CHANNELCAST, NTFY_METADATA, \
    DLSTATUS_METADATA, DLSTATUS_WAITING4HASHCHECK, dlstatus_strings

# DB Tuples
from Tribler.Main.Utility.GuiDBTuples import Torrent, ChannelTorrent, RemoteChannelTorrent, RemoteTorrent
from Tribler.Core.TorrentDef import TorrentDefNoMetainfo


# TODO: not hardcoded please
DOWNLOAD_DIRECTORY = os.path.join(os.getcwdu(), 'Downloads')
DOWNLOAD_UPDATE_DELAY = 5.0

class DownloadManager():
    # Code to make this a singleton
    __single = None

    connected = False

    _dllock = threading.Lock()
    _session = None
    _dispersy = None
    _remote_lock = None

    _misc_db = None
    _torrent_db = None
    _channelcast_db = None
    _votecast_db = None

    _downloads = {}

    def __init__(self, session, xmlrpc=None):
        """
        Constructor for the DownloadManager that loads all db connections.
        :param session: The Tribler session that the DownloadManager should apply to.
        :param xmlrpc: The XML-RPC Manager that the DownloadManager should apply to. If specified, the DownloadManager
        registers its public functions with the XMLRpcManager.
        :return:
        """
        if DownloadManager.__single:
            raise RuntimeError("DownloadManager is singleton")

        self.connected = False

        self._session = session
        self._remote_lock = threading.Lock()

        self._connect()

        if xmlrpc:
            self._xmlrpc_register(xmlrpc)

    def getInstance(*args, **kw):
        if DownloadManager.__single is None:
            DownloadManager.__single = DownloadManager(*args, **kw)
        return DownloadManager.__single
    getInstance = staticmethod(getInstance)

    def delInstance(*args, **kw):
        DownloadManager.__single = None
    delInstance = staticmethod(delInstance)

    def _connect(self):
        """
        Load database handles and Dispersy.
        :return: Nothing.
        """
        if not self.connected:
            self.connected = True
            self._misc_db = self._session.open_dbhandler(NTFY_MISC)
            self._torrent_db = self._session.open_dbhandler(NTFY_TORRENTS)
            self._channelcast_db = self._session.open_dbhandler(NTFY_CHANNELCAST)
            self._votecast_db = self._session.open_dbhandler(NTFY_VOTECAST)

            self._dispersy = self._session.lm.dispersy
        else:
            raise RuntimeError('TorrentManager already connected')

    def _xmlrpc_register(self, xmlrpc):
        """
        Register the public functions in this manager with an XML-RPC Manager.
        :param xmlrpc: The XML-RPC Manager it should register to.
        :return: Nothing.
        """
        xmlrpc.register_function(self.add_torrent, 'downloads.add')
        xmlrpc.register_function(self.remove_torrent, 'downloads.remove')
        xmlrpc.register_function(self.get_progress, 'downloads.get_progress_info')
        xmlrpc.register_function(self.get_progress_all, 'downloads.get_all_progress_info')
        #xmlrpc.register_function(self.get_vod, 'downloads.get_vod_info')
        #xmlrpc.register_function(self.get_full, 'downloads.get_full_info')
        xmlrpc.register_function(self.start_vod, 'downloads.start_vod')
        xmlrpc.register_function(self.stop_vod, 'downloads.stop_vod')
        xmlrpc.register_function(self.get_vod_uri, 'downloads.get_vod_uri')
        xmlrpc.register_function(self.set_state, 'downloads.set_state')

        #test
        xmlrpc.register_function(self.launch_vlc, "downloads.launch_vlc")

    def add_torrent(self, infohash, name):
        """
        Add a download to the download list by its infohash.
        :param infohash: The infohash of the torrent.
        :param name: The name of the torrent.
        :return: Boolean indicating success.
        """

        def add_torrent_callback():
            try:
                bin_infohash = binascii.unhexlify(infohash)

                tdef = TorrentDefNoMetainfo(bin_infohash, name)
                _logger.info("[%s] Adding torrent by magnet link" % infohash)

                defaultDLConfig = DefaultDownloadStartupConfig.getInstance()
                dscfg = defaultDLConfig.copy()

                dscfg.set_dest_dir(DOWNLOAD_DIRECTORY)

                dl = self._session.start_download(tdef, dscfg)
                dl.set_state_callback(self._update_dl_state, delay=1)

            except Exception, e:
                _logger.error("Error adding torrent (infohash=%s,name=%s) (%s)" % (infohash, name, e.args))
                return False

            return True

        self._session.lm.rawserver.add_task(add_torrent_callback, delay=1)
        return True

    def _update_dl_state(self, ds):

        self._dllock.acquire()
        try:
            _logger.info("Got download status callback (%s: %s; %s)" % (type(ds).__name__, ds.get_status(), ds.get_progress()))

            dldict = self._getDownloadState(ds, progress=True)
            if dldict:
                if dldict['infohash'] in self._downloads.keys():
                    self._downloads[dldict['infohash']].update(dldict)
                else:
                    self._downloads[dldict['infohash']] = dldict
            else:
                _logger.warn("Error updating download state")

        finally:
            self._dllock.release()
            return DOWNLOAD_UPDATE_DELAY, False

    def remove_torrent(self, infohash, removecontent):
        """
        Remove a download from the download list by its infohash.
        :param infohash: The infohash of the torrent.
        :return: Boolean indicating success.
        """
        def remove_torrent_callback():
            try:
                _logger.info("Removing torrent with infohash %s" % infohash)
                dl = self._session.get_download(binascii.unhexlify(infohash))
                self._session.remove_download(dl, removecontent)

                if infohash in self._downloads.keys():
                    self._downloads.pop(infohash, None)

                return True

            except Exception, e:
                _logger.error("Couldn't remove torrent with infohash %s (%s)" % (infohash, e.args))
                return False

        self._session.lm.rawserver.add_task(remove_torrent_callback, delay=1)
        return True

    def get_progress(self, infohash):
        """
        Get the progress of a single torrent, by infohash.
        :param infohash: Infohash of the torrent.
        :return: Progress of a torrent or False on failure.
        """
        with self._dllock:
            if infohash in self._downloads.keys():
                return self._downloads[infohash]
            else:
                return False

    def get_progress_all(self):
        """
        Get the progress of all current torrents.
        :return: List of progress torrents.
        """
        with self._dllock:
            return self._downloads.values()

    def get_full(self, infohash):
        """
        Get the full info of a single torrent, by infohash.
        :param infohash: Infohash of the torrent.
        :return: Full info of a torrent or False on failure.
        """
        return self._get_download_info(infohash, {'progress': True, 'files': True, 'network': True})

    def get_vod(self, infohash):
        """
        Get the vod status of a single torrent, by infohash.
        :param infohash: Infohash of the torrent.
        :return: Vod status of a torrent or False on failure.
        """
        return self._get_download_info(infohash, {'vod': True})

    def _get_download_info(self, infohash, args):
        """
        Get the info of a download by its infohash.
        :param infohash: The infohash of the torrent.
        :param args: Dictionary with arguments indicating which info to include.
        :return: Dictionary with information about the download.
        """
        try:
            download = self._session.get_download(binascii.unhexlify(infohash))
            return self._getDownload(download, **args)
        except:
            return False

    def start_vod(self, infohash):
        """
        Set a download to vod mode.
        :param infohash: Infohash of the torrent.
        :return: Vod uri on success, False otherwise.
        """
        try:
            download = self._session.get_download(binascii.unhexlify(infohash))
            download.set_vod_mode(True)
        except:
            return False

        return self.get_vod_uri(infohash)

    def stop_vod(self, infohash):
        """
        Set a download to normal download mode.
        :param infohash: Infohash of the torrent.
        :return: Boolean indicating success.
        """
        try:
            download = self._session.get_download(binascii.unhexlify(infohash))
            download.set_vod_mode(False)
        except:
            return False

        return True

    def get_vod_uri(self, infohash):
        """
        Returns the VOD uri for this torrent.
        :param infohash: Infohash of the torrent.
        :return: Uri that can be used to stream the torrent.
        """
        return "http://127.0.0.1:%s/%s/0" % (self._session.get_videoplayer_port(), infohash)

    def set_state(self, infohash):
        pass

    def _get_torrent_from_infohash(self, infohash):
        dict = self._torrent_db.getTorrent(infohash, keys=['C.torrent_id', 'infohash', 'swift_hash', 'swift_torrent_hash', 'name', 'torrent_file_name', 'length', 'category_id', 'status_id', 'num_seeders', 'num_leechers'])
        if dict:
            t = Torrent(dict['C.torrent_id'], dict['infohash'], dict['swift_hash'], dict['swift_torrent_hash'], dict['name'], dict['torrent_file_name'], dict['length'], dict['category_id'], dict['status_id'], dict['num_seeders'], dict['num_leechers'], None)
            t.misc_db = self._misc_db
            t.torrent_db = self._torrent_db
            t.channelcast_db = self._channelcast_db
            # TODO: ENABLE metadata_db WHEN METADATA COMMUNITY IS ENABLED
            t.metadata_db = None  #self._metadata_db

            # prefetching channel, metadata
            _ = t.channel
            _ = t.metadata
            return t

    """
    def _add_torrent_file(self, torrent_path, destination_path):
        _logger.error("Downloading %s to %s" % (torrent_path, destination_path))

        tdef = TorrentDef.load(torrent_path)
        defaultDLConfig = DefaultDownloadStartupConfig.getInstance()
        dscfg = defaultDLConfig.copy()

        dscfg.set_dest_dir(destination_path)

        dl = self._session.start_download(tdef, dscfg)

        while not dl.handle:
            time.sleep(1)
            _logger.error("Waiting for libtorrent (%s)" % dl.tdef.get_name())

        return dl
    """

    def launch_vlc(self, infohash):
        assert os.environ['ANDROID_HOST'].startswith("ANDROID")
        from vlcutil import launchVLC

        launchVLC(self.get_vod_uri(infohash))

    def _getDownload(self, torrentimpl, vod=False, progress=False, files=False, network=False):
        """
        Convert a LibTorrentDownloadImpl object to a dictionary.
        :param torrentimpl: A LibTorrentDownloadImpl object.
        :param vod: Include info about vod.
        :param progress: Include info about download progress.
        :param files: Include info about files.
        :param network: Include info about network.
        :return: Dictionary with information about the download.
        """
        #progress = infoh, name, speed, eta, progress, size, seeders/leechers
        #vod = vod_eta, vod_stats
        #full = progress + {files, metadata (description, thumbnail), dest}, speed_max

        try:
            dlinfo = {'infohash': binascii.hexlify(torrentimpl.get_def().get_infohash())}

            if progress:
                dlinfo.update({'name': torrentimpl.tdef.get_name(),
                               'progress': torrentimpl.get_progress(),
                               'length': torrentimpl.get_length(),
                               'speed_up': torrentimpl.get_current_speed(UPLOAD),
                               'speed_down': torrentimpl.get_current_speed(DOWNLOAD),
                               'eta': torrentimpl.network_calc_eta(),
                               'status': torrentimpl.get_status(),
                               'status_string': dlstatus_strings[torrentimpl.get_status()],
                               # TODO: return state
                               })

            if vod:
                vod_stats = torrentimpl.network_get_vod_stats()
                dlinfo.update({'vod_eta': torrentimpl.network_calc_prebuf_eta(),
                               'vod_pieces': vod_stats['npieces'],
                               'vod_played': vod_stats['playes'],
                               'vod_firstpiece': vod_stats['firstpiece'],
                               'vod_pos': vod_stats['pos'],
                               'vod_late': vod_stats['late'],
                               'vod_stall': vod_stats['stall'],
                               'vod_dropped': vod_stats['dropped'],
                               'vod_prebuf': vod_stats['prebuf'],
                               })

            if files:
                dlinfo.update({'destination': torrentimpl.get_content_dest(),
                               'speed_up_max': torrentimpl.get_max_desired_speed(UPLOAD),
                               'speed_down_max': torrentimpl.get_max_desired_speed(DOWNLOAD),
                               'files': torrentimpl.get_dest_files(),
                               'magnet_uri': torrentimpl.get_magnet_link(),
                               })

            if network:
                dlinfo.update({'network': torrentimpl.network_create_statistics_reponse()})

            return dlinfo
        except Exception, e:
            print "Error getting TorrentDownloadImpl: %s" % e.args
            return {}

    def _getDownloadState(self, dstate, vod=False, progress=False, files=False, network=False):
        """
        Convert a LibTorrentDownloadImpl object to a dictionary.
        :param dstate: A LibTorrentDownloadImpl object.
        :param vod: Include info about vod.
        :param progress: Include info about download progress.
        :param files: Include info about files.
        :param network: Include info about network.
        :return: Dictionary with information about the download.
        """
        #progress = infoh, name, speed, eta, progress, size, seeders/leechers
        #vod = vod_eta, vod_stats
        #full = progress + {files, metadata (description, thumbnail), dest}, speed_max

        try:
            dlinfo = {'infohash': binascii.hexlify(dstate.get_download().get_def().get_infohash())}

            if progress:
                dlinfo.update({'name': dstate.get_download().get_def().get_name(),
                               'progress': dstate.get_progress(),
                               'length': dstate.get_length(),
                               'speed_up': dstate.get_current_speed(UPLOAD),
                               'speed_down': dstate.get_current_speed(DOWNLOAD),
                               'eta': dstate.get_eta(),
                               'status': dstate.get_status(),
                               'status_string': dlstatus_strings[dstate.get_status()],
                               })

            return dlinfo
        except Exception, e:
            print "Error getting downloadstate: %s" % e.args
            return {}