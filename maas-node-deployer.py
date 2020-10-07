#!/usr/bin/env python

import maas
import time
import yaml
import re
import asyncio

from maas.client import login
from maas.client import connect
from maas.client.enum import LinkMode, InterfaceType, PartitionTableType, NodeStatus, BlockDeviceType, PowerState
from maas.client.utils.maas_async import asynchronous

config = yaml.load(open("config.yaml"),Loader=yaml.BaseLoader)
client = maas.client.connect(config["maas_url"], apikey=config["maas_apikey"])

machines_new = []
machines_commissioned = []
machines_failed = []

for machine in client.machines.list():
    machine_tags = list(map(lambda tag: tag.name, machine.tags))
    for t in config["tag_filter"]:
        if t in machine_tags and machine.status == NodeStatus.NEW:
            machines_new.append(machine)

if len(machines_new) == 0:
    print("No NEW nodes found!")
    exit(1)

@asynchronous
async def commission_nodes():
    for machine in machines_new:
        print("Commissioning %s " % machine.hostname)
        await machine.commission(wait=False)

    while len(machines_new) > 0:
        await asyncio.sleep(5)
        for machine in list(machines_new):
            await machine.refresh()
            if machine.status in [NodeStatus.COMMISSIONING, NodeStatus.TESTING]:
                continue
            elif machine.status == NodeStatus.READY:
                machines_commissioned.append(machine)
                machines_new.remove(machine)
            else:
                machines_failed.append(machine)
                machines_new.remove(machine)

    if len(machines_failed) > 0:
        for machine in machines_failed:
            print("%s failed commissioning with %s" % (machine.hostname, machine.status_name))

@asynchronous
async def deploy_nodes():
    for machine in machines_commissioned:
        print("Deploying %s " % machine.hostname)
        await machine.deploy(wait=False)

    while len(machines_commissioned) > 0:
        await asyncio.sleep(5)
        for machine in list(machines_commissioned):
            await machine.refresh()
            if machine.status in [NodeStatus.DEPLOYING]:
                await machine.query_power_state()

                if machine.power_state == PowerState.OFF:
                    print("hack: Powering on %s" % machine.hostname)
                    # hack around node not powering on after reboot
                    await machine.power_on()
                continue
            elif machine.status == NodeStatus.DEPLOYED:
                print("Machine %s deployed" % machine.hostname)
                machines_commissioned.remove(machine)

commission_nodes()

machines_config = config["machines_config"]
machines_names = list(config["machines_config"])

for machine in machines_commissioned:
    ipmi_conf = machine.get_power_parameters()

    if len(list(ipmi_conf)) == 0:
        print("Enlisted node %s is missing IPMI settings !" % machine.hostname)
        continue

    # configure hostname
    for name in machines_names:
        if not "ipmi_ip" in machines_config[name]:
            print("Configuration for node %s is missing IPMI address !" %name)

            exit(1)
        elif machines_config[name]["ipmi_ip"] == ipmi_conf["power_address"]:
            print("Configuration for %s found" % name)

            machine.hostname = name
            machine.save()
            machines_names.remove(name)
            break

    # configure storage
    if len(machine.block_devices) > 0:
        machine.restore_storage_configuration()

        # wipe out exiting storage layout
        for vg in machine.volume_groups:
            vg.refresh()

            for lv in vg.logical_volumes:
                lv.delete()
            
            vg.delete()

        for disk in machine.block_devices:
            for partition in disk.partitions:
                partition.delete()

        machine.refresh()

        for disk_name, disk_conf in machines_config[machine.hostname]["disks"].items():
            print("Configuring disk %s" % disk_name)

            if "type" in disk_conf:
                if disk_conf["type"] == "vg":
                    vg_disks = []
                    for parent_name, parent_conf in disk_conf["parents"].items():
                        try:
                            machine_disk = machine.block_devices.get_by_name(parent_name)
                        except:
                            print("Invalid configuration for %s" % disk_name)
                            exit(1)

                        # mark boot disk
                        if "boot" in parent_conf:
                            machine_disk.set_as_boot_disk()

                        if "partitions" in parent_conf:
                            for partition in parent_conf["partitions"]:
                                match_size = re.match("^([0-9]+)\%", partition["size"])

                                if match_size == None:
                                    print("Invalid size for %s" % disk_name)
                                    exit(1)

                                part_size = float(match_size.group(1)) * (float(machine_disk.size) - 15728640) / 100.0

                                vg_disks.append(machine_disk.partitions.create(size=int(part_size)))
                        else:
                            vg_disks.append(machine_disk)

                    vg = machine.volume_groups.create(name=disk_name, devices=vg_disks)
                elif disk_conf["type"] == "lv":
                    vg = machine.volume_groups.get_by_name(name=disk_conf["parents"][0])
                    vg.refresh()

                    match_size = re.match("^([0-9]+)\%", disk_conf["size"])

                    if match_size == None:
                        print("Invalid size for %s" % disk_name)
                        exit(1)

                    vol_size = float(match_size.group(1)) * (float(vg.size) - 15728640) / 100.0

                    machine_disk = vg.logical_volumes.create(name=disk_name, size=int(vol_size))
                    machine_disk.format(fstype=disk_conf["fstype"])
                    machine_disk.mount(disk_conf["mount"])
                else:
                    print("Invalid type specified for %s" % disk_name)
            else:
                machine_disk =  machine.block_devices.get_by_name(name=disk_name)
                for partition in disk_conf["partitions"]:
                    if "size" in partition:
                        match_size = re.match("^([0-9]+)\%", partition["size"])

                        if match_size == None:
                            print("Invalid size for %s" % disk_name)
                            exit(1)

                        part_size = float(match_size.group(1)) * (float(machine_disk.size) - 15728640) / 100.0
                        machine_disk.partitions.create(size=int(part_size))
                    if "fstype" in partition:
                        machine_disk.partitions[-1].format(fstype=partition["fstype"])
                    if "mount" in partition:
                        machine_disk.partitions[-1].mount(partition["mount"])

                # mark boot disk
                if "boot" in disk_conf:
                    machine_disk.set_as_boot_disk()

            machine_disk.save()
    else:
        print("No block devices on %s" % machine.hostname)
        exit(1)

    # configure network
    vlans = {}
    for fabric in client.fabrics.list():
        for vlan in fabric.vlans:
            vlans[str(vlan.vid)] = vlan

    if len(machine.interfaces) > 0:
        # clear existing network configuration
        machine.restore_networking_configuration()

        for interface_name, interface_conf in machines_config[machine.hostname]["network"].items():
            print("Configuring network interface %s" % interface_name)

            if "type" in interface_conf:
                if interface_conf["type"] == "bond":
                    try:
                        if_parents = list(map(machine.interfaces.get_by_name, interface_conf["parents"]))
                    except:
                        print("Parent for %s not found on the machine" % interface_name)
                        exit(1)

                    machine_if = machine.interfaces.create(InterfaceType.BOND, name=interface_name,
                            parents=if_parents, bond_mode=interface_conf["mode"])

                    machine_if.save()

                elif interface_conf["type"] == "vlan":
                    try:
                        if_parent = machine.interfaces.get_by_name(interface_conf["parents"][0])
                    except:
                        print("Parent for %s not found on the machine" % interface_name)
                        exit(1)

                    if_parent.vlan = vlans[interface_conf["vid"]]

                    machine_if = machine.interfaces.create(InterfaceType.VLAN, name=interface_name, 
                            parent=if_parent, vlan=vlans[interface_conf["vid"]])

                    machine_if.save()
            else:
                try:
                    machine_if = machine.interfaces.get_by_name(interface_name)
                except:
                    print("Interface %s not found on the machine" % interface)
                    exit(1)

            # configure IP layer
            if "address" in interface_conf and "subnet" in interface_conf:
                subnet = client.subnets.get(interface_conf["subnet"])

                if interface_conf["address"] == "auto":
                    link = machine_if.links.create(LinkMode.AUTO, subnet=subnet.id)
                elif interface_conf["address"] == "dhcp":
                    link = machine_if.links.create(LinkMode.DHCP, subnet=subnet.id)
                else:
                    link = machine_if.links.create(LinkMode.STATIC, subnet=subnet.id, ip_address=interface_conf["address"])

                if "default_gw_if" in interface_conf:
                    link.set_as_default_gateway

            # save configuration
            machine_if.save()

    else:
        print("No network interfaces on %s" % machine.hostname)
        exit(1)

    # configure tags
    system_tags = list(map(lambda t: t.name, client.tags.list()))
    machine_tags = list(map(lambda t: t.name, machine.tags))

    if "tags" in machines_config[machine.hostname]:
        for tag in machines_config[machine.hostname]["tags"]:
            print("Configuring tag %s" % tag)

            if not tag in list(system_tags):
                client.tags.create(tag)

            if not tag in machine_tags:
                machine.tags.add(client.tags.get(name=tag))
    print("Node %s configured" % machine.hostname)

deploy_nodes()
