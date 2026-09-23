class GatewayError(Exception):
    def __init__(self, code, message, status=502):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)
