import 'package:flutter/material.dart';
import '../services/api_service.dart';

class ChatMessage {
  final String role;
  final String content;
  final String? messageId;

  ChatMessage({required this.role, required this.content, this.messageId});
}

class ChatPage extends StatefulWidget {
  final String sessionId;
  final String model;

  const ChatPage({
    super.key,
    required this.sessionId,
    required this.model,
  });

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final TextEditingController _textController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final List<ChatMessage> _messages = [];
  bool _isLoading = false;
  bool _isInitialLoading = true;

  @override
  void initState() {
    super.initState();
    _loadSessionHistory();
  }

  @override
  void dispose() {
    _textController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  /// 加载历史消息
  Future<void> _loadSessionHistory() async {
    setState(() => _isInitialLoading = true);
    try {
      final session = await ApiService.getSession(widget.sessionId);
      final messages = session['messages'] ?? [];
      setState(() {
        _messages.clear();
        for (var msg in messages) {
          _messages.add(ChatMessage(
            role: msg['role'],
            content: msg['content'],
          ));
        }
        _isInitialLoading = false;
      });
      _scrollToBottom();
    } catch (e) {
      setState(() => _isInitialLoading = false);
      _showError('加载历史消息失败: $e');
    }
  }

  /// 发送消息
  Future<void> _sendMessage() async {
    final text = _textController.text.trim();
    if (text.isEmpty || _isLoading) return;

    // 清空输入框
    _textController.clear();

    // 添加用户消息到界面
    setState(() {
      _messages.add(ChatMessage(role: 'user', content: text));
      _isLoading = true;
    });
    _scrollToBottom();

    try {
      // 构建消息列表（发送给 API）
      final apiMessages = _messages.map((m) => {
        'role': m.role,
        'content': m.content,
      }).toList();

      // 调用 API
      final response = await ApiService.sendMessage(
        model: widget.model,
        messages: apiMessages,
        sessionId: widget.sessionId,
      );

      // 添加 AI 回复到界面
      setState(() {
        _messages.add(ChatMessage(
          role: 'assistant',
          content: response['reply'] ?? '',
          messageId: response['message_id'],
        ));
        _isLoading = false;
      });
      _scrollToBottom();
    } catch (e) {
      setState(() {
        _isLoading = false;
        _messages.add(ChatMessage(
          role: 'assistant',
          content: '抱歉，发生错误：$e',
        ));
      });
      _showError('发送消息失败: $e');
    }
  }

  /// 发送反馈
  Future<void> _sendFeedback(ChatMessage message, int rating) async {
    if (message.messageId == null) return;
    try {
      await ApiService.sendFeedback(
        messageId: message.messageId!,
        rating: rating,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('感谢反馈！'), duration: Duration(seconds: 1)),
      );
    } catch (e) {
      if (!mounted) return;
      _showError('反馈提交失败: $e');
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _showError(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), backgroundColor: Colors.red),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('对话'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _loadSessionHistory,
          ),
        ],
      ),
      body: _isInitialLoading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                // 消息列表
                Expanded(
                  child: ListView.builder(
                    controller: _scrollController,
                    itemCount: _messages.length,
                    padding: const EdgeInsets.all(16),
                    itemBuilder: (context, index) {
                      final message = _messages[index];
                      final isUser = message.role == 'user';
                      return _buildMessageBubble(message, isUser);
                    },
                  ),
                ),
                // 加载指示器
                if (_isLoading)
                  const Padding(
                    padding: EdgeInsets.all(8.0),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                        SizedBox(width: 8),
                        Text('正在思考...'),
                      ],
                    ),
                  ),
                // 输入区域
                _buildInputArea(),
              ],
            ),
    );
  }

  /// 构建消息气泡
  Widget _buildMessageBubble(ChatMessage message, bool isUser) {
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.75,
        ),
        child: Column(
          crossAxisAlignment: isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            // 消息内容
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              decoration: BoxDecoration(
                color: isUser ? Colors.blue : Colors.grey[200],
                borderRadius: BorderRadius.circular(18),
              ),
              child: Text(
                message.content,
                style: TextStyle(
                  color: isUser ? Colors.white : Colors.black,
                  fontSize: 16,
                ),
              ),
            ),
            // 反馈按钮（仅对 AI 消息显示）
            if (!isUser && message.messageId != null)
              Padding(
                padding: const EdgeInsets.only(top: 4, left: 8),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    GestureDetector(
                      onTap: () => _sendFeedback(message, 1),
                      child: const Icon(Icons.thumb_up_alt_outlined, size: 18, color: Colors.grey),
                    ),
                    const SizedBox(width: 12),
                    GestureDetector(
                      onTap: () => _sendFeedback(message, -1),
                      child: const Icon(Icons.thumb_down_alt_outlined, size: 18, color: Colors.grey),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  /// 构建输入区域
  Widget _buildInputArea() {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white,
        boxShadow: [
          BoxShadow(
            color: Colors.grey.withValues(alpha: 0.5),
            blurRadius: 4,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: SafeArea(
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _textController,
                decoration: InputDecoration(
                  hintText: '输入消息...',
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Colors.grey[100],
                  contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                ),
                maxLines: 4,
                minLines: 1,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => _sendMessage(),
              ),
            ),
            const SizedBox(width: 8),
            IconButton(
              onPressed: _isLoading ? null : _sendMessage,
              icon: Icon(
                Icons.send,
                color: _isLoading ? Colors.grey : Colors.blue,
              ),
            ),
          ],
        ),
      ),
    );
  }
}