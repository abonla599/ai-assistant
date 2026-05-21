import 'dart:convert';
import 'package:http/http.dart' as http;

class ApiService {
  // 修改为你的后端地址
  static const String baseUrl = 'http://10.0.2.2:8000'; // Android 模拟器
  // static const String baseUrl = 'http://localhost:8000'; // iOS 模拟器 / Web

  /// 获取模型列表
  static Future<List<dynamic>> getModels() async {
    final response = await http.get(Uri.parse('$baseUrl/v1/models'));
    if (response.statusCode == 200) {
      final data = json.decode(response.body);
      return data['models'] ?? [];
    }
    throw Exception('获取模型列表失败');
  }

  /// 创建新会话
  static Future<Map<String, dynamic>> createSession({String model = 'deepseek-chat'}) async {
    final response = await http.post(
      Uri.parse('$baseUrl/v1/sessions?model=$model'),
    );
    if (response.statusCode == 200) {
      return json.decode(response.body)['data'];
    }
    throw Exception('创建会话失败');
  }

  /// 获取会话列表
  static Future<List<dynamic>> getSessions() async {
    final response = await http.get(Uri.parse('$baseUrl/v1/sessions'));
    if (response.statusCode == 200) {
      final data = json.decode(response.body);
      return data['data'] ?? [];
    }
    throw Exception('获取会话列表失败');
  }

  /// 获取会话详情
  static Future<Map<String, dynamic>> getSession(String sessionId) async {
    final response = await http.get(Uri.parse('$baseUrl/v1/sessions/$sessionId'));
    if (response.statusCode == 200) {
      return json.decode(response.body)['data'];
    }
    throw Exception('获取会话详情失败');
  }

  /// 发送消息（非流式）
  static Future<Map<String, dynamic>> sendMessage({
    required String model,
    required List<Map<String, String>> messages,
    String? sessionId,
  }) async {
    final body = {
      'model': model,
      'messages': messages,
      if (sessionId != null) 'session_id': sessionId,
    };

    final response = await http.post(
      Uri.parse('$baseUrl/v1/chat'),
      headers: {'Content-Type': 'application/json'},
      body: json.encode(body),
    );

    if (response.statusCode == 200) {
      return json.decode(response.body);
    }
    throw Exception('发送消息失败');
  }

  /// 发送反馈
  static Future<void> sendFeedback({
    required String messageId,
    required int rating,
    String? comment,
  }) async {
    final body = {
      'message_id': messageId,
      'rating': rating,
      if (comment != null) 'comment': comment,
    };

    await http.post(
      Uri.parse('$baseUrl/v1/feedback'),
      headers: {'Content-Type': 'application/json'},
      body: json.encode(body),
    );
  }
}