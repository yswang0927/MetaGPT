from metagpt.tools.tool_registry import register_tool
from metagpt.utils.report import ServerReporter


# An un-implemented tool reserved for deploying a local service to public
@register_tool(
    include_functions=[
        "deploy_to_public",
    ]
)
class Deployer:
    """Deploy a local service to public. Used only for final deployment, you should NOT use it for development and testing."""

    # yswang add
    chat_id = ""
    role = None

    async def static_server(self, src_path: str) -> str:
        """This function will be implemented in the remote service."""
        return "http://127.0.0.1:9000/index.html"

    async def deploy_to_public(self, dist_dir: str):
        """
        Deploy a web project to public.
        Args:
            dist_dir (str): The dist directory of the web project after run build.
        >>>
            deployer = Deployer("2048_game/dist")
        """
        url = await self.static_server(dist_dir)

        # yswang add
        with ServerReporter() as reporter:
            reporter.set_chat_id(self.chat_id)
            reporter.set_role(self.role)
            reporter.report(url)

        return "The Project is deployed to: " + url + "\n Deployment successed!"

    # yswang add
    def set_chat_id(self, chat_id):
        self.chat_id = chat_id

    def set_role(self, role):
        self.role = role