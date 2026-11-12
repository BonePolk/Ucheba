"""Администратору передаются только категории ошибок, без личных текстов/URL."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select
from sqlalchemy import inspect, text

from common.agent_issues import ISSUE_TOOLS
from common.database import engine
from common.identity import user_id
from common.models.integration import AgentIssue


class IssueInput(BaseModel):
    agent: Literal['Straus', 'Monocle', 'Ameba', 'Arrodes']
    tool_name: str = Field(min_length=1, max_length=60, pattern=r'^[a-z_]+$')
    category: Literal['timeout', 'element_not_found', 'login_failed', 'unsupported_file', 'invalid_format', 'service_unavailable', 'missing_capability', 'other']
    description: str = Field(default='', max_length=500)

    @field_validator('tool_name')
    @classmethod
    def known_tool(cls, value):
        if value not in ISSUE_TOOLS:
            raise ValueError('Неизвестный инструмент')
        return value


def target():
    AgentIssue.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        columns = {column['name'] for column in inspect(connection).get_columns('agentissue')}
        if 'description' not in columns:
            connection.execute(text("ALTER TABLE agentissue ADD COLUMN description TEXT NOT NULL DEFAULT ''"))
    return engine


def report(body: IssueInput):
    with Session(target()) as session:
        row = session.exec(select(AgentIssue).where(AgentIssue.owner_id == user_id(),
            AgentIssue.agent == body.agent, AgentIssue.tool_name == body.tool_name,
            AgentIssue.category == body.category, AgentIssue.resolved == False)).first()
        if row:
            row.description = body.description or row.description
            row.occurrences += 1
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = AgentIssue(owner_id=user_id(), **body.model_dump())
        session.add(row)
        session.commit()
        session.refresh(row)
        return {'reported': True, 'id': row.id}


def list_issues():
    with Session(target()) as session:
        return session.exec(select(AgentIssue).where(AgentIssue.resolved == False)
            .order_by(AgentIssue.updated_at.desc()).limit(100)).all()


def resolve(issue_id):
    with Session(target()) as session:
        row = session.get(AgentIssue, issue_id)
        if row:
            row.resolved = True
            session.add(row)
            session.commit()
    return {'ok': True}
