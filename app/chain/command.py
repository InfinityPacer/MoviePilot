import traceback
from typing import Any, Union, Dict

from app.chain import ChainBase
from app.chain.download import DownloadChain
from app.chain.site import SiteChain
from app.chain.subscribe import SubscribeChain
from app.chain.system import SystemChain
from app.chain.transfer import TransferChain
from app.core.config import settings
from app.core.event import Event as ManagerEvent, eventmanager
from app.core.plugin import PluginManager
from app.helper.message import MessageHelper
from app.log import logger
from app.scheduler import Scheduler
from app.schemas import Notification
from app.schemas.types import EventType, MessageChannel
from app.utils.object import ObjectUtils
from app.utils.singleton import Singleton


class CommandChain(ChainBase, metaclass=Singleton):
    """
    全局命令管理，消费事件
    """
    # 内建命令
    _commands = {}

    def __init__(self):
        # 插件管理器
        super().__init__()
        self.pluginmanager = PluginManager()
        # 定时服务管理
        self.scheduler = Scheduler()
        # 消息管理器
        self.messagehelper = MessageHelper()
        # 内置命令：标准参数 arg_str: str, channel: MessageChannel, userid: Union[str, int] = None, source: str = None
        # 其中 arg_str 为用户输入的参数，channel 为消息渠道，userid 为用户ID，source 为消息来源，arg_str 可选
        self._commands = {
            "/cookiecloud": {
                "id": "cookiecloud",
                "type": "scheduler",
                "description": "同步站点",
                "category": "站点"
            },
            "/sites": {
                "func": SiteChain().remote_list,
                "description": "查询站点",
                "category": "站点",
                "data": {}
            },
            "/site_cookie": {
                "func": SiteChain().remote_cookie,
                "description": "更新站点Cookie",
                "data": {}
            },
            "/site_enable": {
                "func": SiteChain().remote_enable,
                "description": "启用站点",
                "data": {}
            },
            "/site_disable": {
                "func": SiteChain().remote_disable,
                "description": "禁用站点",
                "data": {}
            },
            "/mediaserver_sync": {
                "id": "mediaserver_sync",
                "type": "scheduler",
                "description": "同步媒体服务器",
                "category": "管理"
            },
            "/subscribes": {
                "func": SubscribeChain().remote_list,
                "description": "查询订阅",
                "category": "订阅",
                "data": {}
            },
            "/subscribe_refresh": {
                "id": "subscribe_refresh",
                "type": "scheduler",
                "description": "刷新订阅",
                "category": "订阅"
            },
            "/subscribe_search": {
                "id": "subscribe_search",
                "type": "scheduler",
                "description": "搜索订阅",
                "category": "订阅"
            },
            "/subscribe_delete": {
                "func": SubscribeChain().remote_delete,
                "description": "删除订阅",
                "data": {}
            },
            "/subscribe_tmdb": {
                "id": "subscribe_tmdb",
                "type": "scheduler",
                "description": "订阅元数据更新"
            },
            "/downloading": {
                "func": DownloadChain().remote_downloading,
                "description": "正在下载",
                "category": "管理",
                "data": {}
            },
            "/transfer": {
                "id": "transfer",
                "type": "scheduler",
                "description": "下载文件整理",
                "category": "管理"
            },
            "/redo": {
                "func": TransferChain().remote_transfer,
                "description": "手动整理",
                "data": {}
            },
            "/clear_cache": {
                "func": SystemChain().remote_clear_cache,
                "description": "清理缓存",
                "category": "管理",
                "data": {}
            },
            "/restart": {
                "func": SystemChain().restart,
                "description": "重启系统",
                "category": "管理",
                "data": {}
            },
            "/version": {
                "func": SystemChain().version,
                "description": "当前版本",
                "category": "管理",
                "data": {}
            }
        }
        # 汇总插件命令
        plugin_commands = self.pluginmanager.get_plugin_commands()
        for command in plugin_commands:
            self.register(
                cmd=command.get('cmd'),
                func=self.send_plugin_event,
                desc=command.get('desc'),
                category=command.get('category'),
                data={
                    'etype': command.get('event'),
                    'data': command.get('data')
                }
            )
        # 广播注册命令菜单
        if not settings.DEV:
            self.register_commands(commands=self.get_commands())

    def __run_command(self, command: Dict[str, any], data_str: str = "",
                      channel: MessageChannel = None, source: str = None, userid: Union[str, int] = None):
        """
        运行定时服务
        """
        if command.get("type") == "scheduler":
            # 定时服务
            if userid:
                self.post_message(
                    Notification(
                        channel=channel,
                        source=source,
                        title=f"开始执行 {command.get('description')} ...",
                        userid=userid
                    )
                )

            # 执行定时任务
            self.scheduler.start(job_id=command.get("id"))

            if userid:
                self.post_message(
                    Notification(
                        channel=channel,
                        source=source,
                        title=f"{command.get('description')} 执行完成",
                        userid=userid
                    )
                )
        else:
            # 命令
            cmd_data = command['data'] if command.get('data') else {}
            args_num = ObjectUtils.arguments(command['func'])
            if args_num > 0:
                if cmd_data:
                    # 有内置参数直接使用内置参数
                    data = cmd_data.get("data") or {}
                    data['channel'] = channel
                    data['source'] = source
                    data['user'] = userid
                    if data_str:
                        data['arg_str'] = data_str
                    cmd_data['data'] = data
                    command['func'](**cmd_data)
                elif args_num == 3:
                    # 没有输入参数，只输入渠道来源、用户ID和消息来源
                    command['func'](channel, userid, source)
                elif args_num > 3:
                    # 多个输入参数：用户输入、用户ID
                    command['func'](data_str, channel, userid, source)
            else:
                # 没有参数
                command['func']()

    def get_commands(self):
        """
        获取命令列表
        """
        return self._commands

    def register(self, cmd: str, func: Any, data: dict = None,
                 desc: str = None, category: str = None) -> None:
        """
        注册命令
        """
        self._commands[cmd] = {
            "func": func,
            "description": desc,
            "category": category,
            "data": data or {}
        }

    def get(self, cmd: str) -> Any:
        """
        获取命令
        """
        return self._commands.get(cmd, {})

    def execute(self, cmd: str, data_str: str = "",
                channel: MessageChannel = None, source: str = None,
                userid: Union[str, int] = None) -> None:
        """
        执行命令
        """
        command = self.get(cmd)
        if command:
            try:
                if userid:
                    logger.info(f"用户 {userid} 开始执行：{command.get('description')} ...")
                else:
                    logger.info(f"开始执行：{command.get('description')} ...")

                # 执行命令
                self.__run_command(command, data_str=data_str,
                                   channel=channel, source=source, userid=userid)

                if userid:
                    logger.info(f"用户 {userid} {command.get('description')} 执行完成")
                else:
                    logger.info(f"{command.get('description')} 执行完成")
            except Exception as err:
                logger.error(f"执行命令 {cmd} 出错：{str(err)} - {traceback.format_exc()}")
                self.messagehelper.put(title=f"执行命令 {cmd} 出错",
                                       message=str(err),
                                       role="system")

    @staticmethod
    def send_plugin_event(etype: EventType, data: dict) -> None:
        """
        发送插件命令
        """
        eventmanager.send_event(etype, data)

    @eventmanager.register(EventType.CommandExcute)
    def command_event(self, event: ManagerEvent) -> None:
        """
        注册命令执行事件
        event_data: {
            "cmd": "/xxx args"
        }
        """
        # 命令参数
        event_str = event.event_data.get('cmd')
        # 消息渠道
        event_channel = event.event_data.get('channel')
        # 消息来源
        event_source = event.event_data.get('source')
        # 消息用户
        event_user = event.event_data.get('user')
        if event_str:
            cmd = event_str.split()[0]
            args = " ".join(event_str.split()[1:])
            if self.get(cmd):
                self.execute(cmd=cmd, data_str=args,
                             channel=event_channel, source=event_source, userid=event_user)
