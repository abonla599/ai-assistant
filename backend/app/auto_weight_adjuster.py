# backend/app/auto_weight_adjuster.py
import os
import json
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading
import time

class FeedbackHandler(FileSystemEventHandler):
    """
    继承自 watchdog 的 FileSystemEventHandler，用于处理文件变化事件。
    当 feedback.json 文件被修改时，会调用 on_modified 方法。
    """
    def __init__(self, callback_func):
        self.callback_func = callback_func  # 传入一个回调函数，用于处理反馈数据

    def on_modified(self, event):
        """
        当监控的文件被修改时触发。
        :param event: 包含文件事件信息的对象
        """
        if event.src_path.endswith('feedback.json') and event.is_directory is False:
            print(f"📁 检测到反馈文件 {event.src_path} 被修改！")
            try:
                with open(event.src_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 将解析后的数据传递给回调函数
                    self.callback_func(data)
            except Exception as e:
                print(f"❌ 处理反馈文件时出错: {e}")

def start_feedback_watcher(feedback_file_path='feedback.json', callback=None):
    """
    启动文件监听器。
    :param feedback_file_path: 要监控的反馈文件路径，默认为 'feedback.json'
    :param callback: 当文件被修改时调用的回调函数
    """
    if callback is None:
        callback = lambda x: print("⚠️ 未提供回调函数，仅打印日志")

    # 创建事件处理器
    event_handler = FeedbackHandler(callback)

    # 创建观察者
    observer = Observer()
    observer.schedule(event_handler, path=os.path.dirname(feedback_file_path) or '.', recursive=False)

    # 开始监听
    observer.start()
    print(f"✅ 实时反馈监听器已启动，监控文件: {feedback_file_path}")

    # 在后台线程中运行，避免阻塞主线程
    def run_observer():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

    thread = threading.Thread(target=run_observer, daemon=True)
    thread.start()

    return observer