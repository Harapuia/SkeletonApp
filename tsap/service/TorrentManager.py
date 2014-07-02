# coding: utf-8
# Written by Wendo Sabée
# Manages local and remote torrent searches

import threading
import binascii
from time import time

# Init logger
import logging
_logger = logging.getLogger(__name__)

# Tribler defs
from Tribler.Core.simpledefs import NTFY_MISC, NTFY_TORRENTS, NTFY_MYPREFERENCES, \
    NTFY_VOTECAST, NTFY_CHANNELCAST, NTFY_METADATA, \
    DLSTATUS_METADATA, DLSTATUS_WAITING4HASHCHECK

# DB Tuples
from Tribler.Main.Utility.GuiDBTuples import Torrent, Channel, ChannelTorrent, RemoteChannelTorrent, RemoteTorrent, MetadataModification

# Tribler communities
from Tribler.community.search.community import SearchCommunity
from Tribler.Core.Search.SearchManager import split_into_keywords
from Tribler.dispersy.util import call_on_reactor_thread
from Tribler.Core.CacheDB.sqlitecachedb import bin2str, str2bin, forceAndReturnDBThread, forceDBThread
from Tribler.Category.Category import Category


from BaseManager import BaseManager

class TorrentManager(BaseManager):
    _dispersy = None
    _remote_lock = None

    _misc_db = None
    _torrent_db = None
    _channelcast_db = None
    _votecast_db = None
    _metadata_db = None

    _keywords = []
    _results = []
    _result_infohashes = []

    def _connect(self):
        """
        Load database handles and Dispersy.
        :return: Nothing.
        """
        if not self._connected:
            self._connected = True
            self._remote_lock = threading.Lock()

            self._misc_db = self._session.open_dbhandler(NTFY_MISC)
            self._torrent_db = self._session.open_dbhandler(NTFY_TORRENTS)
            self._metadata_db = self._session.open_dbhandler(NTFY_METADATA)
            self._channelcast_db = self._session.open_dbhandler(NTFY_CHANNELCAST)
            self._votecast_db = self._session.open_dbhandler(NTFY_VOTECAST)

            self._category = Category.getInstance()
            self._category_names = {}
            self._xxx_category = -1
            for key, id in self._misc_db._category_name2id_dict.iteritems():
                self._category_names[id] = key

                if key.lower() == "xxx":
                    self._xxx_category = id

            self._dispersy = self._session.lm.dispersy
        else:
            raise RuntimeError('TorrentManager already connected')

    def _xmlrpc_register(self, xmlrpc):
        """
        Register the public functions in this manager with an XML-RPC Manager.
        :param xmlrpc: The XML-RPC Manager it should register to.
        :return: Nothing.
        """
        xmlrpc.register_function(self.get_local, "torrents.get_local")
        xmlrpc.register_function(self.search_remote, "torrents.search_remote")
        xmlrpc.register_function(self.get_remote_results_count, "torrents.get_remote_results_count")
        xmlrpc.register_function(self.get_remote_results, "torrents.get_remote_results")
        #xmlrpc.register_function(self.get_by_channel, "torrents.get_by_channel")
        xmlrpc.register_function(self.get_full_info, "torrents.get_full_info")

    def get_local(self, filter):
        """
        Search the local torrent database for torrent files by keyword.
        :param filter: (Optional) keyword filter.
        :return: List of torrents in dictionary format.
        """
        keywords = split_into_keywords(unicode(filter))
        keywords = [keyword for keyword in keywords if len(keyword) > 1]

        TORRENT_REQ_COLUMNS = ['T.torrent_id', 'infohash', 'swift_hash', 'swift_torrent_hash', 'T.name', 'torrent_file_name', 'length', 'category_id', 'status_id', 'num_seeders', 'num_leechers', 'C.id', 'T.dispersy_id', 'C.name', 'T.name', 'C.description', 'C.time_stamp', 'C.inserted']
        TUMBNAILTORRENT_REQ_COLUMNS = ['torrent_id', 'Torrent.infohash', 'swift_hash', 'swift_torrent_hash', 'name', 'torrent_file_name', 'length', 'category_id', 'status_id', 'num_seeders', 'num_leechers']

        @forceAndReturnDBThread
        def local_search(keywords):
            begintime = time()

            results = self._torrent_db.searchNames(keywords, doSort=False, keys=TORRENT_REQ_COLUMNS)
            print ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>"
            print results

            begintuples = time()

            if len(results) > 0:
                def create_channel(a):
                    return Channel(*a)

                channels = {}
                for a in results:
                    channel_details = a[-10:]
                    if channel_details[0] and channel_details[0] not in channels:
                        channels[channel_details[0]] = create_channel(channel_details)

                def create_torrent(a):
                    #channel = channels.get(a[-10], False)
                    #if channel and (channel.isFavorite() or channel.isMyChannel()):
                    #    t = ChannelTorrent(*a[:-12] + [channel, None])
                    #else:
                    t = Torrent(*a[:11] + [False])

                    t.misc_db = self._misc_db
                    t.torrent_db = self._torrent_db
                    t.channelcast_db = self._channelcast_db
                    #t.metadata_db = self._metadata_db
                    t.assignRelevance(a[-11])
                    return t

                results = map(create_torrent, results)
            print ">>>>>>> LOCAL RESULTS: %s" % results

            _logger.debug('TorrentSearchGridManager: _doSearchLocalDatabase took: %s of which tuple creation took %s', time() - begintime, time() - begintuples)
            return results

        results = self._prepare_torrents(local_search(keywords))
        print ">>>>>>> LOCAL RESDICT: %s" % results

        return results

    def search_remote(self, keywords):
        """
        Search for torrent files with our Dispersy peers.
        :param keywords: Keyword to search for.
        :return: Number of searches launched, or False on failure.
        """
        try:
            self._set_keywords(keywords)
        except:
            return False

        self._search_remote_on_reactor()

        # TODO: Return an actual value
        return -1

    @call_on_reactor_thread
    def _search_remote_on_reactor(self):
        """
        Do an actual remote search among our Dispersy peers, on the twisted reactor thread. It uses the previously set
        keywords.
        :return: Number of searched launched, or False on failure.
        """

        nr_requests_made = 0

        if self._dispersy:
            for community in self._dispersy.get_communities():
                if isinstance(community, SearchCommunity):
                    nr_requests_made = community.create_search(self._keywords, self._search_remote_callback)
                    if not nr_requests_made:
                        _logger.error("@@@@ Could not send search in SearchCommunity, no verified candidates found")
                    break

            else:
                _logger.error("@@@@ Could not send search in SearchCommunity, community not found")

        else:
            _logger.error("@@@@ Could not send search in SearchCommunity, Dispersy not found")

        _logger.info("@@@@ Made %s requests to the search community" % nr_requests_made)

        # TODO: FIX RETURN VALUE (CURRENTLY ALWAYS NONE)
        return nr_requests_made


    @call_on_reactor_thread
    def _search_remote_callback(self, keywords, results, candidate):
        """
        Callback that is called by Dispersy on incoming Torrent search results.
        :param keywords: Keywords that these results belong to.
        :param results: List of results.
        :param candidate: The peer that has the full torrent file.
        :return: Nothing.
        """
        _logger.info("******************** got %s unfiltered results for %s %s %s" % (len(results), keywords, candidate, time()))

        # Ignore searches we don't want (anymore)
        if not self._keywords == keywords:
            _logger.info("Ignored results for %s, we are looking for %s now" % (keywords, self._keywords))
            return

        for result in results:
            try:
                categories = result[4]
                category_id = self._misc_db.categoryName2Id(categories)

                remoteHit = RemoteTorrent(-1, result[0], result[8], result[9], result[1], result[2], category_id,
                                          self._misc_db.torrentStatusName2Id(u'good'), result[6], result[7],
                                          set([candidate]))

                # Guess matches
                #keywordset = set(keywords)
                #swarmnameset = set(split_into_keywords(remoteHit.name))
                #matches = {'fileextensions': set()}
                #matches['swarmname'] = swarmnameset & keywordset  # all keywords matching in swarmname
                #matches['filenames'] = keywordset - matches['swarmname']  # remaining keywords should thus me matching in filenames or fileextensions

                #if len(matches['filenames']) == 0:
                #    _, ext = os.path.splitext(result[0])
                #    ext = ext[1:]

                #    matches['filenames'] = matches['swarmname']
                #    matches['filenames'].discard(ext)

                #    if ext in keywordset:
                #        matches['fileextensions'].add(ext)
                #remoteHit.assignRelevance(matches)
                remoteHit.misc_db = self._misc_db
                remoteHit.torrent_db = self._torrent_db
                remoteHit.channelcast_db = self._channelcast_db

                if remoteHit.category_id == self._xxx_category and self._category.family_filter_enabled():
                    _logger.info("Ignore XXX torrent: %s" % remoteHit.name)
                else:
                    # Add to result list.
                    self._add_remote_result(remoteHit)
            except:
                pass

        return

    def _add_remote_result(self, torrent):
        """
        Add a result to the local result list, ignoring any duplicates.
        WARNING: Only call when a lock is already acquired.
        :param torrent: Torrent to add to the list.
        :return: Boolean indicating success.
        """
        # TODO: RLocks instead of normal locks.

        try:
            self._remote_lock.acquire()

            # Do not add duplicates
            if torrent.infohash in self._result_infohashes:
                _logger.error("Torrent duplicate: %s [%s]" % (torrent.name, binascii.hexlify(torrent.infohash)))
                return False

            self._results.append(torrent)
            self._result_infohashes.append(torrent.infohash)

            _logger.error("Torrent added: %s [%s]" % (torrent.name, binascii.hexlify(torrent.infohash)))
            return True
        finally:
            self._remote_lock.release()

    def get_remote_results(self):
        """
        Return any results that were found during the last remote search.
        :return: List of Torrent dictionaries.
        """
        return self._prepare_torrents(self._results)

    def get_remote_results_count(self):
        """
        Get the amount of current remote results.
        :return: Integer indicating the number of results.
        """
        return len(self._results)

    def get_full_info(self):
        """
        Get the full info of a torrent from our peers.
        :return: Torrent dictionary.
        """
        # TODO: GET FULL INFO FROM TORRENT
        pass

    def _set_keywords(self, keywords):
        """
        Set the keywords that a next search should use. This clears the previous keywords and results.
        :param keywords: Keyword string that should be searched for.
        :return: Boolean indicating success.
        """
        keywords = split_into_keywords(unicode(keywords))
        keywords = [keyword for keyword in keywords if len(keyword) > 1]

        if keywords == self._keywords:
            return True

        try:
            self._remote_lock.acquire()

            self._keywords = keywords
            self._results = []
            self._result_infohashes = []
        finally:
            self._remote_lock.release()

        return True

    def _prepare_torrents(self, trs):
        """
        Convert a list of Torrent objects to a list of Torrent dictionaries.
        :param trs: List of Torrent objects.
        :return: List of Torrent dictionaries.
        """
        torrents = []
        for tr in trs:
            try:
                torrents.append(self._prepare_torrent(tr))
            except:
                _logger.error("prepare torrent fail: %s" % tr.name)
                pass

        return torrents

    def get_torrent_metadata(self, torrent):
        message_list = self._metadata_db.getMetadataMessageList(
            torrent.infohash, torrent.swift_hash,
            columns=("message_id",))
        if not message_list:
            return []

        metadata_mod_list = []
        for message_id, in message_list:
            data_list = self._metadata_db.getMetadataData(message_id)
            for key, value in data_list:
                metadata_mod_list.append(MetadataModification(torrent, message_id, key, value))

        return metadata_mod_list

    def _prepare_torrent(self, tr):
        """
        Convert a Torrent object to a Torrent dictionary.
        :param trs: Torrent object.
        :return: Torrent dictionary.
        """
        assert isinstance(tr, RemoteTorrent) or isinstance(tr, Torrent) or isinstance(tr, ChannelTorrent)

        """
            self.infohash = infohash
            self.swift_hash = swift_hash
            self.swift_torrent_hash = swift_torrent_hash
            self.torrent_file_name = torrent_file_name
            self.name = name
            self.length = length or 0
            self.category_id = category_id
            self.status_id = status_id

            self.num_seeders = num_seeders or 0
            self.num_leechers = num_leechers or 0

            self.update_torrent_id(torrent_id)
            self.updateChannel(channel)

            self.channeltorrents_id = None
            self.misc_db = None
            self.torrent_db = None
            self.channelcast_db = None
            self.metadata_db = None

            self.relevance_score = None
            self.query_candidates = None
            self._progress = None
            self.dslist = None
            self.magnetstatus = None
        """

        return {'infohash': binascii.hexlify(tr.infohash) if tr.infohash else False,
                'swift_hash': binascii.hexlify(tr.swift_hash) if tr.swift_hash else False,
                'swift_torrent_hash': binascii.hexlify(
                    tr.swift_torrent_hash).upper() if tr.swift_torrent_hash else False,
                'torrent_file_name': tr.torrent_file_name or False,
                'name': tr.name,
                'length': str(tr.length) if tr.length else "-1",
                'category_id': tr.category_id,
                'category': self._category_names[tr.category_id],
                'status_id': tr.status_id,
                'num_seeders': tr.num_seeders if tr.num_seeders else -1,
                'num_leechers': tr.num_leechers if tr.num_leechers else -1,
                'relevance': tr.relevance_score if tr.relevance_score else -1,
        }
