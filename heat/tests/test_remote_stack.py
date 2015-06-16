#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy
import mock
import six

from heatclient import exc
from heatclient.v1 import stacks
from oslo.config import cfg

from heat.common import exception
from heat.common.i18n import _
from heat.common import template_format
from heat.engine import environment
from heat.engine import parser
from heat.engine import resource
from heat.engine.resources import remote_stack
from heat.engine import rsrc_defn
from heat.engine import scheduler
from heat.tests import common as tests_common
from heat.tests import utils


cfg.CONF.import_opt('action_retry_limit', 'heat.common.config')

parent_stack_template = '''
heat_template_version: 2013-05-23
resources:
    remote_stack:
        type: OS::Heat::Stack
        properties:
            context:
                region_name: RegionOne
            template: { get_file: remote_template.yaml }
            timeout: 60
            parameters:
                name: foo
'''

remote_template = '''
heat_template_version: 2013-05-23
parameters:
  name:
    type: string
resources:
  resource1:
    type: GenericResourceType
outputs:
  foo:
    value: bar
'''

bad_template = '''
heat_template_version: 2013-05-26
parameters:
  name:
    type: string
resources:
  resource1:
    type: UnknownResourceType
outputs:
  foo:
    value: bar
'''


def get_stack(stack_id='c8a19429-7fde-47ea-a42f-40045488226c',
              stack_name='teststack', description='No description',
              creation_time='2013-08-04T20:57:55Z',
              updated_time='2013-08-04T20:57:55Z',
              stack_status='CREATE_COMPLETE',
              stack_status_reason='',
              outputs=None):
    action = stack_status[:stack_status.index('_')]
    status = stack_status[stack_status.index('_') + 1:]
    data = {
        'id': stack_id,
        'stack_name': stack_name,
        'description': description,
        'creation_time': creation_time,
        'updated_time': updated_time,
        'stack_status': stack_status,
        'stack_status_reason': stack_status_reason,
        'action': action,
        'status': status,
        'outputs': outputs or None,
    }
    return stacks.Stack(mock.MagicMock(), data)


class FakeClients(object):
    def __init__(self, region_name=None):
        self.region_name = region_name or 'RegionOne'
        self.hc = None
        self.plugin = None

    def heat(self):
        if self.region_name in ['RegionOne', 'RegionTwo']:
            if self.hc is None:
                self.hc = mock.MagicMock()
            return self.hc
        else:
            raise Exception('Failed connecting to Heat')

    def client_plugin(self, name):
        def examine_exception(ex):
            if not isinstance(ex, exc.HTTPNotFound):
                raise ex
        if self.plugin is None:
            self.plugin = mock.MagicMock()
            self.plugin.ignore_not_found.side_effect = examine_exception
        return self.plugin


class RemoteStackTest(tests_common.HeatTestCase):

    def setUp(self):
        super(RemoteStackTest, self).setUp()
        self.this_region = 'RegionOne'
        self.that_region = 'RegionTwo'
        self.bad_region = 'RegionNone'

        cfg.CONF.set_override('action_retry_limit', 0)
        self.parent = None
        self.heat = None
        self.client_plugin = None
        self.this_context = None
        self.old_clients = None

        def unset_clients_property():
            type(self.this_context).clients = self.old_clients

        self.addCleanup(unset_clients_property)

    def initialize(self):
        parent, rsrc = self.create_parent_stack(remote_region='RegionTwo')
        self.parent = parent
        self.heat = rsrc._context().clients.heat()
        self.client_plugin = rsrc._context().clients.client_plugin('heat')

    def create_parent_stack(self, remote_region=None, custom_template=None):
        snippet = template_format.parse(parent_stack_template)
        self.files = {
            'remote_template.yaml': custom_template or remote_template
        }

        region_name = remote_region or self.this_region
        props = snippet['resources']['remote_stack']['properties']

        # context property is not required, default to current region
        if remote_region is None:
            del props['context']
        else:
            props['context']['region_name'] = region_name

        if self.this_context is None:
            self.this_context = utils.dummy_context(
                region_name=self.this_region)

        tmpl = parser.Template(snippet, files=self.files)
        parent = parser.Stack(self.this_context, 'parent_stack', tmpl)

        # parent context checking
        ctx = parent.context.to_dict()
        self.assertEqual(self.this_region, ctx['region_name'])
        self.assertEqual(self.this_context.to_dict(), ctx)

        parent.store()

        resource_defns = parent.t.resource_definitions(parent)
        rsrc = remote_stack.RemoteStack(
            'remote_stack_res',
            resource_defns['remote_stack'],
            parent)

        # remote stack resource checking
        self.assertEqual(60, rsrc.properties.get('timeout'))

        remote_context = rsrc._context()
        hc = FakeClients(rsrc._region_name)
        if self.old_clients is None:
            self.old_clients = type(remote_context).clients
            type(remote_context).clients = mock.PropertyMock(return_value=hc)

        return parent, rsrc

    def create_remote_stack(self):
        # This method default creates a stack on RegionTwo (self.other_region)
        defaults = [get_stack(stack_status='CREATE_IN_PROGRESS'),
                    get_stack(stack_status='CREATE_COMPLETE')]

        def side_effect(*args, **kwargs):
            return defaults.pop(0)

        if self.parent is None:
            self.initialize()

        # prepare clients to return status
        self.heat.stacks.create.return_value = {'stack': get_stack().to_dict()}
        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)
        rsrc = self.parent['remote_stack']
        scheduler.TaskRunner(rsrc.create)()

        return rsrc

    def test_create_remote_stack_default_region(self):
        parent, rsrc = self.create_parent_stack()

        self.assertEqual((rsrc.INIT, rsrc.COMPLETE), rsrc.state)
        self.assertEqual(self.this_region, rsrc._region_name)
        ctx = rsrc.properties.get('context')
        self.assertIsNone(ctx)

        self.assertIsNone(rsrc.validate())

    def test_create_remote_stack_this_region(self):
        parent, rsrc = self.create_parent_stack(remote_region=self.this_region)

        self.assertEqual((rsrc.INIT, rsrc.COMPLETE), rsrc.state)
        self.assertEqual(self.this_region, rsrc._region_name)
        ctx = rsrc.properties.get('context')
        self.assertEqual(self.this_region, ctx['region_name'])

        self.assertIsNone(rsrc.validate())

    def test_create_remote_stack_that_region(self):
        parent, rsrc = self.create_parent_stack(remote_region=self.that_region)

        self.assertEqual((rsrc.INIT, rsrc.COMPLETE), rsrc.state)
        self.assertEqual(self.that_region, rsrc._region_name)
        ctx = rsrc.properties.get('context')
        self.assertEqual(self.that_region, ctx['region_name'])

        self.assertIsNone(rsrc.validate())

    def test_create_remote_stack_bad_region(self):
        parent, rsrc = self.create_parent_stack(remote_region=self.bad_region)

        self.assertEqual((rsrc.INIT, rsrc.COMPLETE), rsrc.state)
        self.assertEqual(self.bad_region, rsrc._region_name)
        ctx = rsrc.properties.get('context')
        self.assertEqual(self.bad_region, ctx['region_name'])

        ex = self.assertRaises(exception.StackValidationFailed,
                               rsrc.validate)
        msg = 'Cannot establish connection to Heat endpoint at region "%s"'\
            % self.bad_region
        self.assertIn(msg, six.text_type(ex))

    def test_remote_validation_failed(self):
        parent, rsrc = self.create_parent_stack(remote_region=self.that_region,
                                                custom_template=bad_template)

        self.assertEqual((rsrc.INIT, rsrc.COMPLETE), rsrc.state)
        self.assertEqual(self.that_region, rsrc._region_name)
        ctx = rsrc.properties.get('context')
        self.assertEqual(self.that_region, ctx['region_name'])

        # not setting or using self.heat because this test case is a special
        # one with the RemoteStack resource initialized but not created.
        heat = rsrc._context().clients.heat()

        # heatclient.exc.BadRequest is the exception returned by a failed
        # validation
        heat.stacks.validate = mock.MagicMock(side_effect=exc.HTTPBadRequest)
        ex = self.assertRaises(exception.StackValidationFailed, rsrc.validate)
        msg = ('Failed validating stack template using Heat endpoint at region'
               ' "%s"') % self.that_region
        self.assertIn(msg, six.text_type(ex))

    def test_create(self):
        rsrc = self.create_remote_stack()

        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        self.assertEqual('c8a19429-7fde-47ea-a42f-40045488226c',
                         rsrc.resource_id)
        registry = rsrc.stack.env.registry
        env = environment.get_custom_environment(registry, {'name': 'foo'})
        args = {
            'stack_name': rsrc.physical_resource_name(),
            'template': template_format.parse(remote_template),
            'timeout_mins': 60,
            'disable_rollback': True,
            'parameters': {'name': 'foo'},
            'files': self.files,
            'environment': env.user_env_as_dict(),
        }
        self.heat.stacks.create.assert_called_with(**args)
        self.assertEqual(2, len(self.heat.stacks.get.call_args_list))

    def test_create_failed(self):
        returns = [get_stack(stack_status='CREATE_IN_PROGRESS'),
                   get_stack(stack_status='CREATE_FAILED',
                             stack_status_reason='Remote stack creation '
                                                 'failed')]

        def side_effect(*args, **kwargs):
            return returns.pop(0)

        # Note: only this test case does a out-of-band intialization, most of
        # the other test cases will have self.parent initialized.
        if self.parent is None:
            self.initialize()

        self.heat.stacks.create.return_value = {'stack': get_stack().to_dict()}
        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)

        rsrc = self.parent['remote_stack']
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.create))
        error_msg = ('ResourceInError: Went to status CREATE_FAILED due to '
                     '"Remote stack creation failed"')
        self.assertEqual(error_msg, six.text_type(error))
        self.assertEqual((rsrc.CREATE, rsrc.FAILED), rsrc.state)

    def test_delete(self):
        returns = [get_stack(stack_status='DELETE_IN_PROGRESS'),
                   get_stack(stack_status='DELETE_COMPLETE')]

        def side_effect_d(*args, **kwargs):
            return returns.pop(0)

        rsrc = self.create_remote_stack()

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect_d)
        self.heat.stacks.delete = mock.MagicMock()
        remote_stack_id = rsrc.resource_id
        scheduler.TaskRunner(rsrc.delete)()

        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.heat.stacks.delete.assert_called_with(stack_id=remote_stack_id)

    def test_delete_already_gone(self):
        def side_effect(*args, **kwargs):
            raise exc.HTTPNotFound()

        rsrc = self.create_remote_stack()

        self.heat.stacks.delete = mock.MagicMock(side_effect=side_effect)
        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)

        remote_stack_id = rsrc.resource_id
        scheduler.TaskRunner(rsrc.delete)()

        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.heat.stacks.delete.assert_called_with(stack_id=remote_stack_id)

    def test_delete_failed(self):
        returns = [get_stack(stack_status='DELETE_IN_PROGRESS'),
                   get_stack(stack_status='DELETE_FAILED',
                             stack_status_reason='Remote stack deletion '
                                                 'failed')]

        def side_effect(*args, **kwargs):
            return returns.pop(0)

        rsrc = self.create_remote_stack()

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)
        self.heat.stacks.delete = mock.MagicMock()

        remote_stack_id = rsrc.resource_id
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.delete))
        error_msg = 'ResourceInError: Went to status DELETE_FAILED due to '\
                    '"Remote stack deletion failed"'
        self.assertIn(error_msg, six.text_type(error))
        self.assertEqual((rsrc.DELETE, rsrc.FAILED), rsrc.state)
        self.heat.stacks.delete.assert_called_with(stack_id=remote_stack_id)
        self.assertEqual(rsrc.resource_id, remote_stack_id)

    def test_attribute(self):
        rsrc = self.create_remote_stack()

        outputs = [
            {
                'output_key': 'foo',
                'output_value': 'bar'
            }
        ]
        created_stack = get_stack(stack_name='stack1', outputs=outputs)
        self.heat.stacks.get = mock.MagicMock(return_value=created_stack)
        self.assertEqual('stack1', rsrc.FnGetAtt('stack_name'))
        self.assertEqual('bar', rsrc.FnGetAtt('outputs')['foo'])
        self.heat.stacks.get.assert_called_with(
            stack_id='c8a19429-7fde-47ea-a42f-40045488226c')

    def test_attribute_failed(self):
        rsrc = self.create_remote_stack()

        error = self.assertRaises(exception.InvalidTemplateAttribute,
                                  rsrc.FnGetAtt, 'non-existent_property')
        self.assertEqual(
            'The Referenced Attribute (remote_stack non-existent_property) is '
            'incorrect.',
            six.text_type(error))

    def test_resume(self):
        stacks = [get_stack(stack_status='RESUME_IN_PROGRESS'),
                  get_stack(stack_status='RESUME_COMPLETE')]

        def side_effect(*args, **kwargs):
            return stacks.pop(0)

        rsrc = self.create_remote_stack()
        rsrc.action = rsrc.SUSPEND

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)
        self.heat.actions.resume = mock.MagicMock()
        scheduler.TaskRunner(rsrc.resume)()

        self.assertEqual((rsrc.RESUME, rsrc.COMPLETE), rsrc.state)
        self.heat.actions.resume.assert_called_with(stack_id=rsrc.resource_id)

    def test_resume_failed(self):
        returns = [get_stack(stack_status='RESUME_IN_PROGRESS'),
                   get_stack(stack_status='RESUME_FAILED',
                             stack_status_reason='Remote stack resume failed')]

        def side_effect(*args, **kwargs):
            return returns.pop(0)

        rsrc = self.create_remote_stack()
        rsrc.action = rsrc.SUSPEND

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)
        self.heat.actions.resume = mock.MagicMock()
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.resume))
        error_msg = ('ResourceInError: Went to status RESUME_FAILED due to '
                     '"Remote stack resume failed"')
        self.assertEqual(error_msg, six.text_type(error))
        self.assertEqual((rsrc.RESUME, rsrc.FAILED), rsrc.state)
        self.heat.actions.resume.assert_called_with(stack_id=rsrc.resource_id)

    def test_resume_failed_not_created(self):
        self.initialize()
        rsrc = self.parent['remote_stack']
        rsrc.action = rsrc.SUSPEND
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.resume))
        error_msg = 'Error: Cannot resume remote_stack, resource not found'
        self.assertEqual(error_msg, six.text_type(error))
        self.assertEqual((rsrc.RESUME, rsrc.FAILED), rsrc.state)

    def test_suspend(self):
        stacks = [get_stack(stack_status='SUSPEND_IN_PROGRESS'),
                  get_stack(stack_status='SUSPEND_COMPLETE')]

        def side_effect(*args, **kwargs):
            return stacks.pop(0)

        rsrc = self.create_remote_stack()

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)
        self.heat.actions.suspend = mock.MagicMock()
        scheduler.TaskRunner(rsrc.suspend)()

        self.assertEqual((rsrc.SUSPEND, rsrc.COMPLETE), rsrc.state)
        self.heat.actions.suspend.assert_called_with(stack_id=rsrc.resource_id)

    def test_suspend_failed(self):
        stacks = [get_stack(stack_status='SUSPEND_IN_PROGRESS'),
                  get_stack(stack_status='SUSPEND_FAILED',
                            stack_status_reason='Remote stack suspend failed')]

        def side_effect(*args, **kwargs):
            return stacks.pop(0)

        rsrc = self.create_remote_stack()

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)
        self.heat.actions.suspend = mock.MagicMock()
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.suspend))
        error_msg = ('ResourceInError: Went to status SUSPEND_FAILED due to '
                     '"Remote stack suspend failed"')
        self.assertEqual(error_msg, six.text_type(error))
        self.assertEqual((rsrc.SUSPEND, rsrc.FAILED), rsrc.state)
        # assert suspend was not called
        self.heat.actions.suspend.assert_has_calls([])

    def test_suspend_failed_not_created(self):
        self.initialize()
        rsrc = self.parent['remote_stack']
        # Note: the resource is not created so far
        self.heat.actions.suspend = mock.MagicMock()
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.suspend))
        error_msg = 'Error: Cannot suspend remote_stack, resource not found'
        self.assertEqual(error_msg, six.text_type(error))
        self.assertEqual((rsrc.SUSPEND, rsrc.FAILED), rsrc.state)
        # assert suspend was not called
        self.heat.actions.suspend.assert_has_calls([])

    def test_update(self):
        stacks = [get_stack(stack_status='UPDATE_IN_PROGRESS'),
                  get_stack(stack_status='UPDATE_COMPLETE')]

        def side_effect(*args, **kwargs):
            return stacks.pop(0)

        rsrc = self.create_remote_stack()

        props = copy.deepcopy(rsrc.parsed_template()['Properties'])
        props['parameters']['name'] = 'bar'
        update_snippet = rsrc_defn.ResourceDefinition(rsrc.name,
                                                      rsrc.type(),
                                                      props)

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)
        scheduler.TaskRunner(rsrc.update, update_snippet)()

        self.assertEqual((rsrc.UPDATE, rsrc.COMPLETE), rsrc.state)
        self.assertEqual('bar', rsrc.properties.get('parameters')['name'])
        registry = rsrc.stack.env.registry
        env = environment.get_custom_environment(registry, {'name': 'bar'})
        fields = {
            'stack_id': rsrc.resource_id,
            'template': template_format.parse(remote_template),
            'timeout_mins': 60,
            'disable_rollback': True,
            'parameters': {'name': 'bar'},
            'files': self.files,
            'environment': env.user_env_as_dict(),
        }
        self.heat.stacks.update.assert_called_with(**fields)
        self.assertEqual(2, len(self.heat.stacks.get.call_args_list))

    def test_update_with_replace(self):
        rsrc = self.create_remote_stack()

        props = copy.deepcopy(rsrc.parsed_template()['Properties'])
        props['context']['region_name'] = 'RegionOne'
        update_snippet = rsrc_defn.ResourceDefinition(rsrc.name,
                                                      rsrc.type(),
                                                      props)
        self.assertRaises(resource.UpdateReplace,
                          scheduler.TaskRunner(rsrc.update, update_snippet))

    def test_update_failed(self):
        stacks = [get_stack(stack_status='UPDATE_IN_PROGRESS'),
                  get_stack(stack_status='UPDATE_FAILED',
                            stack_status_reason='Remote stack update failed')]

        def side_effect(*args, **kwargs):
            return stacks.pop(0)

        rsrc = self.create_remote_stack()

        props = copy.deepcopy(rsrc.parsed_template()['Properties'])
        props['parameters']['name'] = 'bar'
        update_snippet = rsrc_defn.ResourceDefinition(rsrc.name,
                                                      rsrc.type(),
                                                      props)

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect)
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.update,
                                                       update_snippet))
        error_msg = _('ResourceInError: Went to status UPDATE_FAILED due to '
                      '"Remote stack update failed"')
        self.assertEqual(error_msg, six.text_type(error))
        self.assertEqual((rsrc.UPDATE, rsrc.FAILED), rsrc.state)
        self.assertEqual(2, len(self.heat.stacks.get.call_args_list))

    def test_stack_status_error(self):
        returns = [get_stack(stack_status='DELETE_IN_PROGRESS'),
                   get_stack(stack_status='UPDATE_COMPLETE')]

        def side_effect_d(*args, **kwargs):
            return returns.pop(0)

        rsrc = self.create_remote_stack()

        self.heat.stacks.get = mock.MagicMock(side_effect=side_effect_d)
        self.heat.stacks.delete = mock.MagicMock()
        remote_stack_id = rsrc.resource_id
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.delete))
        error_msg = _('ResourceUnknownStatus: Resource failed - Unknown '
                      'status UPDATE_COMPLETE')
        self.assertEqual(error_msg, six.text_type(error))
        self.heat.stacks.delete.assert_called_with(stack_id=remote_stack_id)
