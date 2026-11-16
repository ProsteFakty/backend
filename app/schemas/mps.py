from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

VoteValue = Literal["for", "against", "abstained", "absent"]


class MpVoteOut(BaseModel):
    """How an MP voted on one bill as a whole."""

    sitting: int = Field(description="Sitting number")
    voting_number: int = Field(description="Voting number within the sitting")
    held_at: datetime = Field(description="Warsaw local time")
    print_number: str = Field(description="Print that started the legislative process")
    title: str = Field(description="Official bill title")
    summary: str | None = Field(description="2-sentence plain-language summary; null until generated")
    source_url: str
    vote: VoteValue = Field(description="What the MP did; absent = did not vote")
    club: str | None = Field(description="MP's club at the time of the voting")
    club_position: Literal["for", "against", "abstained", "split", "absent"] | None = Field(
        description="What most of the club's voting members did; null when the MP had no club"
    )
