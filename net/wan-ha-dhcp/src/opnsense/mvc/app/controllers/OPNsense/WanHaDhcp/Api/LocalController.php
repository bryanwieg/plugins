<?php

namespace OPNsense\WanHaDhcp\Api;

use OPNsense\Base\ApiMutableModelControllerBase;

class LocalController extends ApiMutableModelControllerBase
{
    protected static $internalModelClass = '\\OPNsense\\WanHaDhcp\\Local';
    protected static $internalModelName = 'wanhalocal';
}
