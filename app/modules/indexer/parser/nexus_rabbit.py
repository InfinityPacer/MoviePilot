# -*- coding: utf-8 -*-
import json
from typing import Optional

from app.log import logger
from app.modules.indexer.parser import SiteSchema
from app.modules.indexer.parser.nexus_php import NexusPhpSiteUserInfo


class NexusRabbitSiteUserInfo(NexusPhpSiteUserInfo):
    schema = SiteSchema.NexusRabbit

    def _parse_site_page(self, html_text: str):
        super()._parse_site_page(html_text)
        self._torrent_seeding_page = f"getusertorrentlistajax.php?page=1&limit=5000000&type=seeding&uid={self.userid}"
        self._torrent_seeding_headers = {"Accept": "application/json, text/javascript, */*; q=0.01"}

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: bool = False) -> Optional[str]:
        """
        做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """

        try:
            torrents = json.loads(html_text).get('data')
        except Exception as e:
            logger.error(f"解析做种信息失败: {str(e)}")
            return

        page_seeding_size = 0
        page_seeding_info = []

        page_seeding = len(torrents)
        for torrent in torrents:
            seeders = int(torrent.get('seeders', 0))
            size = int(torrent.get('size', 0))
            page_seeding_size += int(torrent.get('size', 0))

            page_seeding_info.append([seeders, size])

        self.seeding += page_seeding
        self.seeding_size += page_seeding_size
        self.seeding_info.extend(page_seeding_info)
