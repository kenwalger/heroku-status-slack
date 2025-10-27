"""
Heroku API client for Heroku Monitoring Bot.

This module provides a client interface for interacting with the Heroku API
to fetch app information, dynos, releases, and configuration.
"""
import logging
import requests
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class HerokuAPIClient:
    """Client for interacting with the Heroku Platform API."""
    
    BASE_URL = 'https://api.heroku.com'

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            'Accept': 'application/vnd.heroku+json; version=3',
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        """
        Make a request to the Heroku API.

        Args:
            method (str): HTTP method, e.g., 'GET', 'POST'.
            endpoint (str): API endpoint path.
            **kwargs: Additional arguments for requests.request.

        Returns:
            Optional[dict]: Parsed JSON response or None on failure.
        """
        url = f"{self.BASE_URL}{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Heroku API request failed: {e}")
            return None

    def get_app_info(self, app_name: str) -> Optional[dict]:
        """
        Get general information for a Heroku app.

        Args:
            app_name (str): Heroku app name.

        Returns:
            Optional[dict]: App info dictionary or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}')

    def get_dynos(self, app_name: str) -> Optional[List[dict]]:
        """
        Get the dyno states for a Heroku app.

        Args:
            app_name (str): The Heroku app name.

        Returns:
            Optional[List[dict]]: List of dyno info dictionaries or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/dynos')

    def get_releases(self, app_name: str, limit: int = 5) -> Optional[List[dict]]:
        """
        Get recent releases for a Heroku app.

        Args:
            app_name (str): Heroku app name.
            limit (int): Maximum number of releases to fetch.

        Returns:
            Optional[List[dict]]: List of release dictionaries or None if request fails.
        """
        releases = self._request('GET', f'/apps/{app_name}/releases')
        if releases:
            return sorted(releases, key=lambda r: r['version'], reverse=True)[:limit]
        return []

    def get_addons(self, app_name: str) -> Optional[List[dict]]:
        """
        Get installed add-ons for a Heroku app.

        Args:
            app_name (str): Heroku app name.

        Returns:
            Optional[List[dict]]: List of add-on dictionaries or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/addons')

    def get_config_vars(self, app_name: str) -> Optional[dict]:
        """
        Get configuration variables for a Heroku app.

        Args:
            app_name (str): Heroku app name.

        Returns:
            Optional[dict]: Dictionary of config vars or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/config-vars')

    def get_formation(self, app_name: str) -> Optional[List[dict]]:
        """
        Get the dyno formation (type/size/quantity) for a Heroku app.

        Args:
            app_name (str): Heroku app name.

        Returns:
            Optional[List[dict]]: List of formation dictionaries or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/formation')

    def update_config_vars(self, app_name: str, config_vars: Dict[str, str]) -> Optional[dict]:
        """
        Update configuration variables for a Heroku app.

        Args:
            app_name (str): Heroku app name.
            config_vars (Dict[str, str]): Dictionary of config vars to set/update.

        Returns:
            Optional[dict]: Updated config vars dictionary or None if request fails.
        """
        return self._request('PATCH', f'/apps/{app_name}/config-vars', json=config_vars)

