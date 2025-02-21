from typing import Any, Optional

from app.db import DbOper
from app.db.models.plugindata import PluginData


class PluginDataOper(DbOper):
    """
    插件数据管理
    """

    def save(self, plugin_id: str, key: str, value: Any, instance_id: Optional[str] = None):
        """
        保存插件数据
        :param plugin_id: 插件id
        :param key: 数据key
        :param value: 数据值
        :param instance_id: 实例ID
        """
        plugin_id = self.get_plugin_id(plugin_id, instance_id)
        plugin = PluginData.get_plugin_data_by_key(self._db, plugin_id, key)
        if plugin:
            plugin.update(self._db, {
                "value": value
            })
        else:
            PluginData(plugin_id=plugin_id, key=key, value=value).create(self._db)

    def get_data(self, plugin_id: str, key: str = None, instance_id: Optional[str] = None) -> Any:
        """
        获取插件数据
        :param plugin_id: 插件id
        :param key: 数据key
        :param instance_id: 实例ID
        """
        plugin_id = self.get_plugin_id(plugin_id, instance_id)
        if key:
            data = PluginData.get_plugin_data_by_key(self._db, plugin_id, key)
            if not data:
                return None
            return data.value
        else:
            return PluginData.get_plugin_data(self._db, plugin_id)

    def del_data(self, plugin_id: str, key: str = None, instance_id: Optional[str] = None) -> Any:
        """
        删除插件数据
        :param plugin_id: 插件id
        :param key: 数据key
        :param instance_id: 实例ID
        """
        plugin_id = self.get_plugin_id(plugin_id, instance_id)
        if key:
            PluginData.del_plugin_data_by_key(self._db, plugin_id, key)
        else:
            PluginData.del_plugin_data(self._db, plugin_id)

    def truncate(self):
        """
        清空插件数据
        """
        PluginData.truncate(self._db)

    def get_data_all(self, plugin_id: str, instance_id: Optional[str] = None) -> Any:
        """
        获取插件所有数据
        :param plugin_id: 插件id
        :param instance_id: 实例ID
        """
        plugin_id = self.get_plugin_id(plugin_id, instance_id)
        return PluginData.get_plugin_data_by_plugin_id(self._db, plugin_id)

    @staticmethod
    def get_plugin_id(plugin_id: str, instance_id: Optional[str] = None) -> Optional[str]:
        """
        获取插件标识
        :param plugin_id: 插件ID
        :param instance_id: 实例ID
        """
        if not instance_id or instance_id == "default":
            return plugin_id
        return f"{plugin_id}.{instance_id}"
