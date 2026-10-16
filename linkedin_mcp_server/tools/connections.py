"""Read-only tools for connection collection and contact enrichment."""

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import AuthenticationError
from linkedin_mcp_server.dependencies import get_ready_extractor, handle_auth_error
from linkedin_mcp_server.error_handler import raise_tool_error
from linkedin_mcp_server.scraping.identifiers import normalize_person_identifier


def register_connections_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register the read-only connections tools."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Get My Connections",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_my_connections(
        ctx: Context,
        limit: Annotated[int, Field(ge=0, le=1000)] = 0,
        max_scrolls: Annotated[int, Field(ge=1, le=100)] = 50,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Collect the signed-in user's connection cards from one page."""
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_my_connections"
            )
            await ctx.report_progress(
                progress=0, total=100, message="Loading connections page"
            )
            result = await extractor.collect_connections(
                limit=limit, max_scrolls=max_scrolls
            )
            await ctx.report_progress(progress=100, total=100, message="Complete")
            return result
        except AuthenticationError as error:
            try:
                await handle_auth_error(error, ctx)
            except Exception as relogin_error:
                raise_tool_error(relogin_error, "get_my_connections")
        except Exception as error:
            raise_tool_error(error, "get_my_connections")

    @mcp.tool(
        timeout=tool_timeout,
        title="Extract Contact Details",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "scraping"},
        exclude_args=["extractor"],
    )
    async def extract_contact_details(
        usernames: str,
        ctx: Context,
        chunk_size: Annotated[int, Field(ge=1, le=50)] = 5,
        chunk_delay: Annotated[float, Field(ge=0, le=300)] = 30.0,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Enrich comma-separated profiles with visible contact details."""
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="extract_contact_details"
            )
            normalized = [
                normalize_person_identifier(item.strip())
                for item in usernames.split(",")
                if item.strip()
            ]
            username_list = list(dict.fromkeys(normalized))
            if not username_list:
                return {
                    "error": "invalid_input",
                    "message": "No valid usernames provided. Pass comma-separated usernames.",
                }

            total = len(username_list)
            await ctx.report_progress(
                progress=0,
                total=total,
                message=f"Starting enrichment of {total} profiles",
            )

            async def on_progress(completed: int, progress_total: int) -> None:
                await ctx.report_progress(
                    progress=completed,
                    total=progress_total,
                    message=f"Enriched {completed}/{progress_total} profiles",
                )

            result = await extractor.enrich_contacts(
                usernames=username_list,
                chunk_size=chunk_size,
                chunk_delay=chunk_delay,
                progress_cb=on_progress,
            )
            completed = result["total"]
            message = (
                "Complete"
                if not result.get("rate_limited")
                else f"Stopped early due to rate limit ({completed}/{total} processed)"
            )
            await ctx.report_progress(progress=completed, total=total, message=message)
            return result
        except AuthenticationError as error:
            try:
                await handle_auth_error(error, ctx)
            except Exception as relogin_error:
                raise_tool_error(relogin_error, "extract_contact_details")
        except Exception as error:
            raise_tool_error(error, "extract_contact_details")
