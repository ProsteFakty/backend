from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int
    pages: int


class SittingListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    title: str
    start_date: date
    end_date: date
    status: Literal["planned", "in_progress", "finished"]
    description: str | None = Field(description="Short plain-language summary of the sitting; null until generated")
    votings_count: int


class Votes(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    yes: int
    no: int
    abstain: int
    absent: int


class ClubVotes(Votes):
    club: str
    position: Literal["for", "against", "abstained", "split", "absent"] = Field(
        description="What most of the club's voting members did"
    )


class BillOut(BaseModel):
    print_number: str
    title: str
    summary: str | None = Field(description="2-sentence plain-language summary; null until generated")
    result: Literal["passed", "rejected", "pending"] = Field(
        description="Outcome of the vote on the whole bill; pending if it was not held at this sitting"
    )
    votes: Votes | None = Field(description="Totals of the vote on the whole bill; null when pending")
    turnout: float | None = Field(description="Share of MPs who voted, 0-1; null when pending")
    clubs: list[ClubVotes] | None = Field(description="How each club voted; null when pending")
    source_url: str


class SittingDetail(SittingListItem):
    turnout: float | None = Field(description="Average turnout across all votings of the sitting, 0-1")
    source_url: str
    bills: list[BillOut]
