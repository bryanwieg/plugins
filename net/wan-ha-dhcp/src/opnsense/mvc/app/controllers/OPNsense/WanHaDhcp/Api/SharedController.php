<?php

namespace OPNsense\WanHaDhcp\Api;

use OPNsense\Base\ApiMutableModelControllerBase;

class SharedController extends ApiMutableModelControllerBase
{
    protected static $internalModelClass = '\\OPNsense\\WanHaDhcp\\Shared';
    protected static $internalModelName = 'wanhashared';
}
