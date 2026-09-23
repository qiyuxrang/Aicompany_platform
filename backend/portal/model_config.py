from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models


class Provider(models.Model):
    class Protocol(models.TextChoices):
        OPENAI_CHAT = "openai_chat", "OpenAI Chat 兼容接口"

    code = models.SlugField("服务商编码", unique=True)
    name = models.CharField("服务商名称", max_length=100)
    protocol = models.CharField("接口协议", max_length=32, choices=Protocol, default=Protocol.OPENAI_CHAT,
        help_text="仅支持 OpenAI Chat 兼容接口，不代表支持所有厂商的原生协议。")
    base_url = models.URLField("接口基础地址", max_length=500,
        help_text="使用规范 HTTPS 地址；网络访问允许名单由独立 FastAPI 服务管理。")
    api_key_env = models.CharField("密钥环境变量名", max_length=100,
        validators=[RegexValidator(r"^PORTAL_MODEL_KEY_[A-Z0-9_]+$", "请输入 PORTAL_MODEL_KEY_ 开头的大写环境变量名。")],
        help_text="仅填写独立 FastAPI 服务环境中的变量名，不是门户进程环境。Key 不入库，请勿填写明文密钥。")
    enabled = models.BooleanField("启用", default=False)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "模型服务商"
        verbose_name_plural = "模型服务商"

    def clean(self):
        super().clean()
        from .model_gateway import validate_provider_url

        validate_provider_url(self.base_url)

    def __str__(self):
        return f"服务商 #{self.pk}"


class GatewayModel(models.Model):
    class TokenParameter(models.TextChoices):
        MAX_TOKENS = "max_tokens", "max_tokens"
        MAX_COMPLETION_TOKENS = "max_completion_tokens", "max_completion_tokens"

    name = models.CharField("模型名称", max_length=100)
    provider = models.ForeignKey(Provider, verbose_name="模型服务商", on_delete=models.PROTECT)
    model_name = models.CharField("远程模型标识", max_length=200)
    supports_text = models.BooleanField("支持文本", default=True)
    enabled = models.BooleanField("启用", default=False)
    timeout_seconds = models.PositiveIntegerField("超时秒数", default=20,
        validators=[MinValueValidator(1), MaxValueValidator(60)])
    max_output_tokens = models.PositiveIntegerField("最大输出令牌数", default=1024,
        validators=[MinValueValidator(1), MaxValueValidator(8192)])
    token_parameter = models.CharField("令牌限制参数", max_length=32, choices=TokenParameter,
        default=TokenParameter.MAX_TOKENS)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "网关模型"
        verbose_name_plural = "网关模型"

    def __str__(self):
        return f"模型 #{self.pk}"


class ModelRoute(models.Model):
    code = models.SlugField("路由编码", unique=True)
    name = models.CharField("路由名称", max_length=100)
    module = models.ForeignKey("portal.Module", verbose_name="业务模块", on_delete=models.PROTECT)
    model = models.ForeignKey(GatewayModel, verbose_name="网关模型", on_delete=models.PROTECT)
    enabled = models.BooleanField("启用", default=False)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "业务模型路由"
        verbose_name_plural = "业务模型路由"

    def __str__(self):
        return f"路由 #{self.pk}"


class ModelCallLog(models.Model):
    class Purpose(models.TextChoices):
        TEST = "test", "连接测试"
        BUSINESS = "business", "业务调用"

    created_at = models.DateTimeField("调用时间", auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="操作者", on_delete=models.PROTECT, null=True, blank=True)
    route = models.ForeignKey(ModelRoute, verbose_name="业务模型路由", on_delete=models.PROTECT, null=True, blank=True)
    model = models.ForeignKey(GatewayModel, verbose_name="网关模型", on_delete=models.PROTECT, null=True, blank=True)
    purpose = models.CharField("调用用途", max_length=16, choices=Purpose)
    status = models.CharField("调用状态", max_length=32)
    duration_ms = models.PositiveIntegerField("耗时（毫秒）")
    prompt_tokens = models.PositiveBigIntegerField("输入令牌数", null=True, blank=True)
    completion_tokens = models.PositiveBigIntegerField("输出令牌数", null=True, blank=True)

    class Meta:
        verbose_name = "模型调用日志"
        verbose_name_plural = "模型调用日志"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"调用日志 #{self.pk}"
