import signal
import sys
from types import FrameType

from fastapi import FastAPI

from app.core.config import settings, global_vars
from app.core.module import ModuleManager
from app.utils.system import SystemUtils

# SitesHelper涉及资源包拉取，提前引入并容错提示
try:
    from app.helper.sites import SitesHelper
except ImportError as e:
    error_message = f"错误: {str(e)}\n站点认证及索引相关资源导入失败，请尝试重建容器或手动拉取资源"
    print(error_message, file=sys.stderr)
    sys.exit(1)

from app.core.event import EventManager
from app.core.plugin import PluginManager
from app.helper.thread import ThreadHelper
from app.helper.display import DisplayHelper
from app.helper.resource import ResourceHelper
from app.helper.message import MessageHelper
from app.scheduler import Scheduler
from app.monitor import Monitor
from app.command import Command, CommandChian
from app.schemas import Notification, NotificationType


def start_frontend():
    """
    启动前端服务
    """
    # 仅Windows可执行文件支持内嵌nginx
    if not SystemUtils.is_frozen() \
            or not SystemUtils.is_windows():
        return
    # 临时Nginx目录
    nginx_path = settings.ROOT_PATH / 'nginx'
    if not nginx_path.exists():
        return
    # 配置目录下的Nginx目录
    run_nginx_dir = settings.CONFIG_PATH.with_name('nginx')
    if not run_nginx_dir.exists():
        # 移动到配置目录
        SystemUtils.move(nginx_path, run_nginx_dir)
    # 启动Nginx
    import subprocess
    subprocess.Popen("start nginx.exe",
                     cwd=run_nginx_dir,
                     shell=True)


def stop_frontend():
    """
    停止前端服务
    """
    if not SystemUtils.is_frozen() \
            or not SystemUtils.is_windows():
        return
    import subprocess
    subprocess.Popen(f"taskkill /f /im nginx.exe", shell=True)


def check_auth():
    """
    检查认证状态
    """
    if SitesHelper().auth_level < 2:
        err_msg = "用户认证失败，站点相关功能将无法使用！"
        MessageHelper().put(f"注意：{err_msg}", title="用户认证", role="system")
        CommandChian().post_message(
            Notification(
                mtype=NotificationType.Manual,
                title="MoviePilot用户认证",
                text=err_msg,
                link=settings.MP_DOMAIN('#/site')
            )
        )


def singal_handle():
    """
    监听停止信号
    """

    def stop_event(signum: int, _: FrameType):
        """
        SIGTERM信号处理
        """
        print(f"接收到停止信号：{signum}，正在停止系统...")
        global_vars.stop_system()

    # 设置信号处理程序
    signal.signal(signal.SIGTERM, stop_event)
    signal.signal(signal.SIGINT, stop_event)


def shutdown_modules(app: FastAPI):
    """
    服务关闭
    """
    # 停止模块
    ModuleManager().stop()
    # 停止插件
    PluginManager().stop()
    PluginManager().stop_monitor()
    # 停止事件消费
    EventManager().stop()
    # 停止虚拟显示
    DisplayHelper().stop()
    # 停止定时服务
    Scheduler().stop()
    # 停止监控
    Monitor().stop()
    # 停止线程池
    ThreadHelper().shutdown()
    # 停止前端服务
    stop_frontend()


def start_modules(app: FastAPI):
    """
    启动模块
    """
    # 虚拟显示
    DisplayHelper()
    # 站点管理
    SitesHelper()
    # 资源包检测
    ResourceHelper()
    # 加载模块
    ModuleManager()
    # 启动事件消费
    EventManager().start()
    # 安装在线插件
    PluginManager().sync()
    # 加载插件
    PluginManager().start()
    # 启动监控任务
    Monitor()
    # 启动定时服务
    Scheduler()
    # 加载命令
    Command()
    # 启动前端服务
    start_frontend()
    # 检查认证状态
    check_auth()
    # 监听停止信号
    singal_handle()
