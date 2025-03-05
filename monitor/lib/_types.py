from typing import Literal, NotRequired, TypedDict


class EmbedAuthorField(TypedDict):
    name: str
    url: NotRequired[str]
    icon_url: NotRequired[str]


class EmbedField(TypedDict):
    name: str
    value: str
    inline: bool


class _EmbedImageField(TypedDict):
    url: str


EmbedThumbnailField = _EmbedImageField
EmbedImageField = _EmbedImageField


class EmbedFooterField(TypedDict):
    text: str
    icon_url: NotRequired[str]


class Embed(TypedDict):
    color: NotRequired[int]
    author: NotRequired[EmbedAuthorField]
    title: str
    url: NotRequired[str]
    description: NotRequired[str]
    fields: NotRequired[list[EmbedField]]
    thumbnail: NotRequired[EmbedThumbnailField]
    image: NotRequired[EmbedImageField]
    footer: NotRequired[EmbedFooterField]
    timestamp: NotRequired[str]


class AllowedMentions(TypedDict):
    parse: list[Literal["roles", "users", "everyone"]]
    roles: NotRequired[list[int]]
    users: NotRequired[list[int]]


class WebhookPayload(TypedDict):
    username: NotRequired[str]
    avatar_url: NotRequired[str]
    content: NotRequired[str]
    embeds: NotRequired[list[Embed]]
    tts: NotRequired[bool]
