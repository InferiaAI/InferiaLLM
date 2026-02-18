from sqlalchemy import Column, String, DateTime, func
from ..database import Base
from inferia.services.api_gateway.db.security import EncryptedJSON

class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True, index=True)
    value = Column(EncryptedJSON, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
