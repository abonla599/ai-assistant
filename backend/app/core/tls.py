"""出网 HTTPS 的信任锚。

默认走 certifi 那份自带根证书包，本机不适用：卡巴斯基的「加密连接扫描」会在路上
换一张它自己签发的证书，而 certifi 里不可能有它的自签根，于是每一次模型调用都是
CERTIFICATE_VERIFY_FAILED。ssl.create_default_context() 读的是操作系统那套根证书库
——用户既然把这张根装进了系统可信根，就是明确接受过这个中间人存在。

这里只是不再自作主张地换一套更窄的信任集，verify_mode 与 check_hostname 一律保持
默认（必须验证书、必须对主机名）。
"""
import ssl


def system_ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()
