from fastapi import Query

from app.models.subscription_template import SubscriptionTemplateListQuery, SubscriptionTemplateSimpleListQuery

from ._common import make_query_dependency, query_param

get_subscription_template_list_query = make_query_dependency(
    SubscriptionTemplateListQuery,
    field_overrides={
        "ids": Query(None),
        "offset": Query(None),
        "limit": Query(None),
    },
)
get_subscription_template_simple_list_query = make_query_dependency(
    SubscriptionTemplateSimpleListQuery,
    field_overrides={
        "ids": Query(None),
        "offset": Query(None),
        "limit": Query(None),
        "search": Query(None),
        "sort": query_param(str | None, None),
        "all": Query(False),
    },
)
