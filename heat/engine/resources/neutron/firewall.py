# vim: tabstop=4 shiftwidth=4 softtabstop=4
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

from heat.engine import clients
from heat.engine.resources.neutron import neutron
from heat.engine import scheduler

if clients.neutronclient is not None:
    from neutronclient.common.exceptions import NeutronClientException

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class Firewall(neutron.NeutronResource):
    """
    A resource for the Firewall resource in Neutron FWaaS.
    """

    properties_schema = {'name': {'Type': 'String',
                                  'UpdateAllowed': True},
                         'description': {'Type': 'String',
                                         'UpdateAllowed': True},
                         'admin_state_up': {'Type': 'Boolean',
                                            'Default': True,
                                            'UpdateAllowed': True},
                         'firewall_policy_id': {'Type': 'String',
                                                'Required': True,
                                                'UpdateAllowed': True}}

    attributes_schema = {
        'name': _('Name for the Firewall.'),
        'description': _('Description of the Firewall.'),
        'admin_state_up': _('The administrative state of the Firewall.'),
        'firewall_policy_id': _('Unique identifier of the FirewallPolicy '
                                'used to  create the Firewall.'),
        'status': _('The status of the Firewall.'),
        'tenant_id': _('Id of the tenant owning the Firewall.'),
        'show': _('All attributes.'),
    }

    update_allowed_keys = ('Properties',)

    def _show_resource(self):
        return self.neutron().show_firewall(self.resource_id)['firewall']

    def handle_create(self):
        props = self.prepare_properties(
            self.properties,
            self.physical_resource_name())
        firewall = self.neutron().create_firewall({'firewall': props})[
            'firewall']
        self.resource_id_set(firewall['id'])

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if prop_diff:
            self.neutron().update_firewall(
                self.resource_id, {'firewall': prop_diff})

    def handle_delete(self):
        client = self.neutron()
        try:
            client.delete_firewall(self.resource_id)
        except NeutronClientException as ex:
            if ex.status_code != 404:
                raise ex
        else:
            return scheduler.TaskRunner(self._confirm_delete)()


class FirewallPolicy(neutron.NeutronResource):
    """
    A resource for the FirewallPolicy resource in Neutron FWaaS.
    """

    properties_schema = {'name': {'Type': 'String',
                                  'UpdateAllowed': True},
                         'description': {'Type': 'String',
                                         'UpdateAllowed': True},
                         'shared': {'Type': 'Boolean',
                                    'Default': False,
                                    'UpdateAllowed': True},
                         'audited': {'Type': 'Boolean',
                                     'Default': False,
                                     'UpdateAllowed': True},
                         'firewall_rules': {'Type': 'List',
                                            'Required': True,
                                            'UpdateAllowed': True}}

    attributes_schema = {
        'name': _('Name for the FirewallPolicy.'),
        'description': _('Description of the FirewallPolicy.'),
        'firewall_rules': _('List of FirewallRules in this FirewallPolicy.'),
        'shared': _('Shared status of this FirewallPolicy.'),
        'audited': _('Audit status of this FirewallPolicy.'),
        'tenant_id': _('Id of the tenant owning the FirewallPolicy.')
    }

    update_allowed_keys = ('Properties',)

    def _show_resource(self):
        return self.neutron().show_firewall_policy(self.resource_id)[
            'firewall_policy']

    def handle_create(self):
        props = self.prepare_properties(
            self.properties,
            self.physical_resource_name())
        firewall_policy = self.neutron().create_firewall_policy(
            {'firewall_policy': props})['firewall_policy']
        self.resource_id_set(firewall_policy['id'])

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if prop_diff:
            self.neutron().update_firewall_policy(
                self.resource_id, {'firewall_policy': prop_diff})

    def handle_delete(self):
        client = self.neutron()
        try:
            client.delete_firewall_policy(self.resource_id)
        except NeutronClientException as ex:
            if ex.status_code != 404:
                raise ex
        else:
            return scheduler.TaskRunner(self._confirm_delete)()


class FirewallRule(neutron.NeutronResource):
    """
    A resource for the FirewallRule resource in Neutron FWaaS.
    """

    properties_schema = {'name': {'Type': 'String',
                                  'UpdateAllowed': True},
                         'description': {'Type': 'String',
                                         'UpdateAllowed': True},
                         'shared': {'Type': 'Boolean',
                                    'Default': False,
                                    'UpdateAllowed': True},
                         'protocol': {'Type': 'String',
                                      'AllowedValues': ['tcp', 'udp', 'icmp',
                                                        None],
                                      'UpdateAllowed': True,
                                      'Default': None},
                         'ip_version': {'Type': 'String',
                                        'UpdateAllowed': True,
                                        'AllowedValues': ['4', '6'],
                                        'Default': '4'},
                         'source_ip_address': {'Type': 'String',
                                               'UpdateAllowed': True,
                                               'Default': None},
                         'destination_ip_address': {'Type': 'String',
                                                    'UpdateAllowed': True,
                                                    'Default': None},
                         'source_port': {'Type': 'String',
                                         'UpdateAllowed': True,
                                         'Default': None},
                         'destination_port': {'Type': 'String',
                                              'UpdateAllowed': True,
                                              'Default': None},
                         'action': {'Type': 'String',
                                    'AllowedValues': ['allow', 'deny'],
                                    'Default': 'deny',
                                    'UpdateAllowed': True},
                         'enabled': {'Type': 'Boolean',
                                     'UpdateAllowed': True,
                                     'Default': True}}

    attributes_schema = {
        'name': _('Name for the FirewallRule.'),
        'description': _('Description of the FirewallRule.'),
        'firewall_policy_id': _('Unique identifier of the FirewallPolicy to '
                                'which this FirewallRule belongs.'),
        'shared': _('Shared status of this FirewallRule.'),
        'protocol': _('Protocol value for this FirewallRule.'),
        'ip_version': _('Ip_version for this FirewallRule.'),
        'source_ip_address': _('Source ip_address for this FirewallRule.'),
        'destination_ip_address': _('Destination ip_address for this '
                                    'FirewallRule.'),
        'source_port': _('Source port range for this FirewallRule.'),
        'destination_port': _('Destination port range for this FirewallRule.'),
        'action': _('Allow or deny action for this FirewallRule.'),
        'enabled': _('Indicates whether this FirewallRule is enabled or not.'),
        'position': _('Position of the rule within the FirewallPolicy.'),
        'tenant_id': _('Id of the tenant owning the Firewall.')
    }

    update_allowed_keys = ('Properties',)

    def _show_resource(self):
        return self.neutron().show_firewall_rule(
            self.resource_id)['firewall_rule']

    def handle_create(self):
        props = self.prepare_properties(
            self.properties,
            self.physical_resource_name())
        firewall_rule = self.neutron().create_firewall_rule(
            {'firewall_rule': props})['firewall_rule']
        self.resource_id_set(firewall_rule['id'])

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if prop_diff:
            self.neutron().update_firewall_rule(
                self.resource_id, {'firewall_rule': prop_diff})

    def handle_delete(self):
        client = self.neutron()
        try:
            client.delete_firewall_rule(self.resource_id)
        except NeutronClientException as ex:
            if ex.status_code != 404:
                raise ex
        else:
            return scheduler.TaskRunner(self._confirm_delete)()


def resource_mapping():
    if clients.neutronclient is None:
        return {}

    return {
        'OS::Neutron::Firewall': Firewall,
        'OS::Neutron::FirewallPolicy': FirewallPolicy,
        'OS::Neutron::FirewallRule': FirewallRule,
    }
