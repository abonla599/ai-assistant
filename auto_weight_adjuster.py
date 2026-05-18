import json
import os
import time
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import chromadb
from chromadb.utils import embedding_functions
from memory_weight_updater import update_memory_weights_from_feedback  # 复用你第三步的逻辑

FEEDBACK_FILE = "feedback.json"
DATA_DIR = "./data"
COLLECTION_NAME = "user_memories"

class FeedbackHandler(FileSystemEventHandler):
    """监听 feedback.json 文件的修改事件"""
    def __init__(self):
        self.last_known_content = self.read_feedback_file()
        self.last_known_mtime = 0  # 记录上次修改时间
        self.process_existing = True  # 程序启动时处理已有反馈

    def read_feedback_file(self):
        if os.path.exists(FEEDBACK_FILE):
            try:
                with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def on_modified(self, event):
        if event.src_path.endswith(FEEDBACK_FILE):
            # 防抖：检查文件修改时间，确保文件完全写入
            current_mtime = os.path.getmtime(FEEDBACK_FILE)
            if current_mtime == self.last_known_mtime:
                return
            self.last_known_mtime = current_mtime
            # 等待 0.5 秒，确保文件写入完全
            time.sleep(0.5)
            new_content = self.read_feedback_file()
            if new_content != self.last_known_content:
                self.process_new_feedback(new_content)
                self.last_known_content = new_content

    def process_existing_feedback(self):
        """处理已存在的反馈（启动时调用）"""
        if os.path.exists(FEEDBACK_FILE):
            current_content = self.read_feedback_file()
            if current_content:
                print(f"[启动时] 发现已存在的 {len(current_content)} 条反馈，开始处理...")
                # 记录当前内容，避免重复处理
                self.last_known_content = current_content.copy()
                self.process_new_feedback(current_content)
            else:
                print("[启动时] feedback.json 为空，无反馈需要处理。")

    def process_new_feedback(self, new_content):
        """处理新增的反馈"""
        if not new_content:
            return
        # 找出新增的反馈项（没有 time 字段标记，通过比较内容判断）
        new_items = []
        if not self.last_known_content:
            new_items = new_content
        else:
            # 通过长度或最后几条判断新增
            if len(new_content) > len(self.last_known_content):
                new_items = new_content[len(self.last_known_content):]
            else:
                # 简单处理：如果有变化且长度没变，可能是修改了，但这种情况较少
                if new_content != self.last_known_content:
                    new_items = new_content
        if new_items:
            print(f"[{datetime.now()}] 检测到 {len(new_items)} 条新的反馈，开始更新记忆权重...")
            # 调用你第三步写好的权重更新器
            update_memory_weights_from_feedback()
            # 可选：打印处理结果
            print(f"[{datetime.now()}] 记忆权重更新完成。")
        else:
            print(f"[{datetime.now()}] 反馈文件有变化，但无新增条目。")

def start_feedback_watcher():
    """启动文件监听器"""
    print(f"[{datetime.now()}] 正在启动反馈监听器...")
    event_handler = FeedbackHandler()
    
    # 先处理已存在的反馈
    event_handler.process_existing_feedback()
    
    observer = Observer()
    observer.schedule(event_handler, path=".", recursive=False)
    observer.start()
    print(f"[{datetime.now()}] 反馈监听器已启动，正在监控 {FEEDBACK_FILE} 文件...")
    return observer, event_handler

if __name__ == "__main__":
    observer, handler = start_feedback_watcher()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("停止监听...")
        observer.stop()
    observer.join()