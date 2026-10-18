import logging

# 测试期间不输出控制器的 warning 日志（生产环境由 __main__ 配置）
_logger = logging.getLogger("gpu_fan_controller")
_logger.addHandler(logging.NullHandler())
_logger.propagate = False
