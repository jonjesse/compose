# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import unicode_literals

import datetime
import json
import os.path
import re
import signal
import subprocess
import time
from collections import Counter
from collections import namedtuple
from functools import reduce
from operator import attrgetter

import pytest
import six
import yaml
from docker import errors

from .. import mock
from ..helpers import BUSYBOX_IMAGE_WITH_TAG
from ..helpers import create_host_file
from compose.cli.command import get_project
from compose.config.errors import DuplicateOverrideFileFound
from compose.container import Container
from compose.project import OneOffFilter
from compose.utils import nanoseconds_from_time_seconds
from tests.integration.testcases import DockerClientTestCase
from tests.integration.testcases import get_links
from tests.integration.testcases import is_cluster
from tests.integration.testcases import no_cluster
from tests.integration.testcases import pull_busybox
from tests.integration.testcases import SWARM_SKIP_RM_VOLUMES
from tests.integration.testcases import v2_1_only
from tests.integration.testcases import v2_2_only
from tests.integration.testcases import v2_only
from tests.integration.testcases import v3_only

DOCKER_COMPOSE_EXECUTABLE = 'docker-compose'

ProcessResult = namedtuple('ProcessResult', 'stdout stderr')


BUILD_CACHE_TEXT = 'Using cache'
BUILD_PULL_TEXT = 'Status: Image is up to date for busybox:1.27.2'
COMPOSE_COMPATIBILITY_DICT = {
    'version': '2.3',
    'volumes': {'foo': {'driver': 'default'}},
    'networks': {'bar': {}},
    'services': {
        'foo': {
            'command': '/bin/true',
            'image': 'alpine:3.10.1',
            'scale': 3,
            'restart': 'always:7',
            'mem_limit': '300M',
            'mem_reservation': '100M',
            'cpus': 0.7,
            'volumes': ['foo:/bar:rw'],
            'networks': {'bar': None},
        }
    },
}


def start_process(base_dir, options):
    proc = subprocess.Popen(
        [DOCKER_COMPOSE_EXECUTABLE] + options,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=base_dir)
    print("Running process: %s" % proc.pid)
    return proc


def wait_on_process(proc, returncode=0, stdin=None):
    stdout, stderr = proc.communicate(input=stdin)
    if proc.returncode != returncode:
        print("Stderr: {}".format(stderr))
        print("Stdout: {}".format(stdout))
        assert proc.returncode == returncode
    return ProcessResult(stdout.decode('utf-8'), stderr.decode('utf-8'))


def dispatch(base_dir, options, project_options=None, returncode=0, stdin=None):
    project_options = project_options or []
    proc = start_process(base_dir, project_options + options)
    return wait_on_process(proc, returncode=returncode, stdin=stdin)


def wait_on_condition(condition, delay=0.1, timeout=40):
    start_time = time.time()
    while not condition():
        if time.time() - start_time > timeout:
            raise AssertionError("Timeout: %s" % condition)
        time.sleep(delay)


def kill_service(service):
    for container in service.containers():
        if container.is_running:
            container.kill()


class ContainerCountCondition(object):

    def __init__(self, project, expected):
        self.project = project
        self.expected = expected

    def __call__(self):
        return len([c for c in self.project.containers() if c.is_running]) == self.expected

    def __str__(self):
        return "waiting for counter count == %s" % self.expected


class ContainerStateCondition(object):

    def __init__(self, client, name, status):
        self.client = client
        self.name = name
        self.status = status

    def __call__(self):
        try:
            if self.name.endswith('*'):
                ctnrs = self.client.containers(all=True, filters={'name': self.name[:-1]})
                if len(ctnrs) > 0:
                    container = self.client.inspect_container(ctnrs[0]['Id'])
                else:
                    return False
            else:
                container = self.client.inspect_container(self.name)
            return container['State']['Status'] == self.status
        except errors.APIError:
            return False

    def __str__(self):
        return "waiting for container to be %s" % self.status


class CLITestCase(DockerClientTestCase):

    def setUp(self):
        super(CLITestCase, self).setUp()
        self.base_dir = 'tests/fixtures/simple-composefile'
        self.override_dir = None

    def tearDown(self):
        if self.base_dir:
            self.project.kill()
            self.project.down(None, True)

            for container in self.project.containers(stopped=True, one_off=OneOffFilter.only):
                container.remove(force=True)
            networks = self.client.networks()
            for n in networks:
                if n['Name'].split('/')[-1].startswith('{}_'.format(self.project.name)):
                    self.client.remove_network(n['Name'])
            volumes = self.client.volumes().get('Volumes') or []
            for v in volumes:
                if v['Name'].split('/')[-1].startswith('{}_'.format(self.project.name)):
                    self.client.remove_volume(v['Name'])
        if hasattr(self, '_project'):
            del self._project

        super(CLITestCase, self).tearDown()

    @property
    def project(self):
        # Hack: allow project to be overridden
        if not hasattr(self, '_project'):
            self._project = get_project(self.base_dir, override_dir=self.override_dir)
        return self._project

    def dispatch(self, options, project_options=None, returncode=0, stdin=None):
        return dispatch(self.base_dir, options, project_options, returncode, stdin)

    def execute(self, container, cmd):
        # Remove once Hijack and CloseNotifier sign a peace treaty
        self.client.close()
        exc = self.client.exec_create(container.id, cmd)
        self.client.exec_start(exc)
        return self.client.exec_inspect(exc)['ExitCode']

    def lookup(self, container, hostname):
        return self.execute(container, ["nslookup", hostname]) == 0

    def test_help(self):
        self.base_dir = 'tests/fixtures/no-composefile'
        result = self.dispatch(['help', 'up'], returncode=0)
        assert 'Usage: up [options] [--scale SERVICE=NUM...] [SERVICE...]' in result.stdout
        # Prevent tearDown from trying to create a project
        self.base_dir = None

    def test_quiet_build(self):
        self.base_dir = 'tests/fixtures/build-args'
        result = self.dispatch(['build'], None)
        quietResult = self.dispatch(['build', '-q'], None)
        assert result.stdout != ""
        assert quietResult.stdout == ""

    def test_help_nonexistent(self):
        self.base_dir = 'tests/fixtures/no-composefile'
        result = self.dispatch(['help', 'foobar'], returncode=1)
        assert 'No such command' in result.stderr
        self.base_dir = None

    def test_shorthand_host_opt(self):
        self.dispatch(
            ['-H={0}'.format(os.environ.get('DOCKER_HOST', 'unix://')),
             'up', '-d'],
            returncode=0
        )

    def test_shorthand_host_opt_interactive(self):
        self.dispatch(
            ['-H={0}'.format(os.environ.get('DOCKER_HOST', 'unix://')),
             'run', 'another', 'ls'],
            returncode=0
        )

    def test_host_not_reachable(self):
        result = self.dispatch(['-H=tcp://doesnotexist:8000', 'ps'], returncode=1)
        assert "Couldn't connect to Docker daemon" in result.stderr

    def test_host_not_reachable_volumes_from_container(self):
        self.base_dir = 'tests/fixtures/volumes-from-container'

        container = self.client.create_container(
            'busybox', 'true', name='composetest_data_container',
            host_config={}
        )
        self.addCleanup(self.client.remove_container, container)

        result = self.dispatch(['-H=tcp://doesnotexist:8000', 'ps'], returncode=1)
        assert "Couldn't connect to Docker daemon" in result.stderr

    def test_config_list_services(self):
        self.base_dir = 'tests/fixtures/v2-full'
        result = self.dispatch(['config', '--services'])
        assert set(result.stdout.rstrip().split('\n')) == {'web', 'other'}

    def test_config_list_volumes(self):
        self.base_dir = 'tests/fixtures/v2-full'
        result = self.dispatch(['config', '--volumes'])
        assert set(result.stdout.rstrip().split('\n')) == {'data'}

    def test_config_quiet_with_error(self):
        self.base_dir = None
        result = self.dispatch([
            '-f', 'tests/fixtures/invalid-composefile/invalid.yml',
            'config', '--quiet'
        ], returncode=1)
        assert "'notaservice' must be a mapping" in result.stderr

    def test_config_quiet(self):
        self.base_dir = 'tests/fixtures/v2-full'
        assert self.dispatch(['config', '--quiet']).stdout == ''

    def test_config_stdin(self):
        config = b"""version: "3.7"
services:
  web:
    image: nginx
  other:
    image: alpine
"""
        result = self.dispatch(['-f', '-', 'config', '--services'], stdin=config)
        assert set(result.stdout.rstrip().split('\n')) == {'web', 'other'}

    def test_config_with_hash_option(self):
        self.base_dir = 'tests/fixtures/v2-full'
        result = self.dispatch(['config', '--hash=*'])
        for service in self.project.get_services():
            assert '{} {}\n'.format(service.name, service.config_hash) in result.stdout

        svc = self.project.get_service('other')
        result = self.dispatch(['config', '--hash=other'])
        assert result.stdout == '{} {}\n'.format(svc.name, svc.config_hash)

    def test_config_default(self):
        self.base_dir = 'tests/fixtures/v2-full'
        result = self.dispatch(['config'])
        # assert there are no python objects encoded in the output
        assert '!!' not in result.stdout

        output = yaml.safe_load(result.stdout)
        expected = {
            'version': '2.0',
            'volumes': {'data': {'driver': 'local'}},
            'networks': {'front': {}},
            'services': {
                'web': {
                    'build': {
                        'context': os.path.abspath(self.base_dir),
                    },
                    'networks': {'front': None, 'default': None},
                    'volumes_from': ['service:other:rw'],
                },
                'other': {
                    'image': BUSYBOX_IMAGE_WITH_TAG,
                    'command': 'top',
                    'volumes': ['/data'],
                },
            },
        }
        assert output == expected
