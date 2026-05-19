import 'package:flutter/material.dart';
import '../services/api_service.dart';
import 'chat_page.dart';

class SessionsPage extends StatefulWidget {
  const SessionsPage({super.key});

  @override
  State<SessionsPage> createState() => _SessionsPageState();
}

class _SessionsPageState extends State<SessionsPage> {
  List<dynamic> _sessions = [];
  bool _isLoading = true;
  String _selectedModel = 'deepseek-chat';

  @override
  void initState() {
    super.initState();
    _loadSessions();
  }

  Future<void> _loadSessions() async {
    setState(() => _isLoading = true);
    try {
      final sessions = await ApiService.getSessions();
      setState(() {
        _sessions = sessions;
        _isLoading = false;
      });
    } catch (e) {
      setState(() => _isLoading = false);
      _showError('加载会话列表失败: $e');
    }
  }

  Future<void> _createNewSession() async {
    try {
      final session = await ApiService.createSession(model: _selectedModel);
      final sessionId = session['session_id'];
      if (!mounted) return;
      // 跳转到聊天页面
      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (context) => ChatPage(
            sessionId: sessionId,
            model: _selectedModel,
          ),
        ),
      ).then((_) => _loadSessions()); // 返回时刷新列表
    } catch (e) {
      if (!mounted) return;
      _showError('创建会话失败: $e');
    }
  }

  void _showError(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), backgroundColor: Colors.red),
    );
  }

  String _formatDate(String? dateStr) {
    if (dateStr == null) return '';
    try {
      final date = DateTime.parse(dateStr);
      return '${date.month}/${date.day} ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
    } catch (e) {
      return dateStr;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('AI 助手'),
        actions: [
          // 模型选择器
          PopupMenuButton<String>(
            onSelected: (value) {
              setState(() => _selectedModel = value);
            },
            itemBuilder: (context) => [
              const PopupMenuItem(value: 'deepseek-chat', child: Text('DeepSeek')),
              const PopupMenuItem(value: 'gpt-4o', child: Text('GPT-4o')),
              const PopupMenuItem(value: 'gpt-3.5-turbo', child: Text('GPT-3.5')),
            ],
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(
                children: [
                  Text(_selectedModel),
                  const Icon(Icons.arrow_drop_down),
                ],
              ),
            ),
          ),
        ],
      ),
      // 新建对话按钮
      floatingActionButton: FloatingActionButton(
        onPressed: _createNewSession,
        child: const Icon(Icons.add),
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _sessions.isEmpty
              ? const Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(Icons.chat_bubble_outline, size: 64, color: Colors.grey),
                      SizedBox(height: 16),
                      Text('暂无对话', style: TextStyle(color: Colors.grey, fontSize: 18)),
                      SizedBox(height: 8),
                      Text('点击右下角按钮开始新对话',
                          style: TextStyle(color: Colors.grey)),
                    ],
                  ),
                )
              : RefreshIndicator(
                  onRefresh: _loadSessions,
                  child: ListView.builder(
                    itemCount: _sessions.length,
                    itemBuilder: (context, index) {
                      final session = _sessions[index];
                      return ListTile(
                        leading: const CircleAvatar(
                          child: Icon(Icons.chat),
                        ),
                        title: Text(
                          session['title'] ?? '未命名对话',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        subtitle: Text(
                          '${session['model'] ?? ''} · ${_formatDate(session['created_at'])}',
                        ),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () {
                          Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (context) => ChatPage(
                                sessionId: session['session_id'],
                                model: session['model'] ?? 'deepseek-chat',
                              ),
                            ),
                          ).then((_) => _loadSessions());
                        },
                      );
                    },
                  ),
                ),
    );
  }
}